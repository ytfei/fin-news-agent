"""Embedding 客户端：直连火山方舟多模态向量化接口。

doubao-embedding-vision 与旧文本模型（doubao-embedding-text-240715）的关键差异：

* 接口：`POST {base_url}/embeddings/multimodal`（不是 OpenAI 兼容的 `/embeddings`）
* input 为**对象数组** `[{"type": "text", "text": "..."}]`，不是字符串数组
* 维度由请求参数 `dimensions` 指定（1024 或 2048），不再是模型固定值
* **单样本语义**：一次请求的 input 数组表示「一个多模态样本的若干部分」
  （文本/图片/视频混合），返回的是这一个样本的**一个**融合向量 —— 响应 `data`
  字段是对象 `{"embedding": [...]}` 而非 OpenAI 的数组 `[{"embedding": ...}]`。
  因此无法像 OpenAI 那样一次请求批量文本，必须逐条请求（这里用 asyncio.gather
  并发逐条）。

并发模型：
* `embedding_batch_size` 已弃用：单条资讯分块通常只有 3~15 块，按资讯分批并发
  的窗口太小，模型侧 QPS 上不去。现在所有待向量化文本统一汇入一个**进程级
  信号量闸门**（`embedding_concurrency`），批处理时多条资讯的 chunk 请求共享
  同一闸门，全局 in-flight 请求数精确受控在模型配额之内，吞吐显著提升。
* 审计日志攒批：每条 embedding 调用只向进程内 pending 队列追加一条记录，
  由 `flush_logs()` 一次性批量写库，避免「每条请求一次数据库往返」抢占连接池。
  `embed_one()`（检索 / QA 查询）保持即时写，行为与旧版一致。

保留自建 Embedder 的原因：
* 写入前的维度校验（维度不一致必须终止，否则污染向量索引）
"""
from __future__ import annotations

import asyncio
import random
import time
import uuid
from typing import Any

import httpx

from fin_news.agents.llm.client import LLMUnavailable
from fin_news.agents.llm.pricing import calc_cost_cent
from fin_news.core.config import Settings, get_settings
from fin_news.core.db import session_scope
from fin_news.core.logging import current_run_id, get_logger
from fin_news.models.event import LLMCallLog

logger = get_logger("agents.embedding")


class DimensionMismatch(ValueError):
    """向量维度与配置不一致（禁止写入，避免污染索引）。"""


class _TokenBucket:
    """进程级 QPS 令牌桶：限制每秒请求数，弥补并发闸门不控 QPS 的缺口。

    并发上限只能约束「同一时刻有多少请求在飞」，无法约束「每秒发出多少请求」；
    当每个请求耗时很短时，低并发也可能产生很高的 QPS 打满模型配额。
    """

    def __init__(self, qps: float) -> None:
        self.qps = max(0.0, float(qps))
        self._tokens = self.qps
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(self.qps, self._tokens + (now - self._updated) * self.qps)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.qps
            await asyncio.sleep(wait)


class Embedder:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._rate_limiter: _TokenBucket | None = (
            _TokenBucket(self.settings.embedding_qps) if self.settings.embedding_qps > 0 else None
        )
        # 进程内攒批的审计日志；由 flush_logs() 一次批量落库
        self._pending_logs: list[LLMCallLog] = []
        self._flush_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    @property
    def client(self) -> httpx.AsyncClient:
        """复用 httpx.AsyncClient（embedding 调用频繁，避免每次建连）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds)
        return self._client

    @property
    def _sem(self) -> asyncio.Semaphore:
        """进程级并发闸门：同时 in-flight 的 embedding 请求不超过配置上限。

        挂在 Embedder 单例上，批处理时多条资讯的 chunk 请求共享同一闸门；
        多 worker 进程各自独立，需按「进程数 × embedding_concurrency
        ≤ 模型侧配额」设定配置。
        """
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max(1, self.settings.embedding_concurrency))
        return self._semaphore

    async def close(self) -> None:
        """兜底：先批量落库审计日志，再关闭 httpx 连接。"""
        await self.flush_logs()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _endpoint(self) -> tuple[str, dict[str, str]]:
        provider = self.settings.embedding_provider
        cfg = self.settings.provider(provider)
        if not cfg.api_key:
            raise LLMUnavailable(f"embedding provider {provider} 未配置 api_key")
        url = f"{cfg.base_url.rstrip('/')}/embeddings/multimodal"
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }
        return url, headers

    def _payload(self, text: str) -> dict[str, object]:
        model = self.settings.model_for(self.settings.embedding_provider, "embedding")
        return {
            "model": model,
            "input": [{"type": "text", "text": text}],
            "encoding_format": "float",
            "dimensions": self.settings.embedding_dim,
            "sparse_embedding": {"type": "disabled"},
        }

    # ------------------------------------------------------------------
    async def embed(self, texts: list[str], *, auto_flush: bool = True) -> list[list[float]]:
        """把全部文本放入受限并发池逐条请求（单样本语义，不可请求内批量）。

        任一条请求失败会等其余请求完成后再抛错（结果丢弃），保证调用方拿到的
        永远是「全部成功」的向量或一个明确异常，不会出现半批状态。
        默认结束后批量落库审计日志；批处理场景传 auto_flush=False，由批次
        结束统一 flush 一次，避免每条请求一次数据库往返。
        """
        if not texts:
            return []
        model = self.settings.model_for(self.settings.embedding_provider, "embedding")
        cleaned = [t.replace("\n", " ").strip() or " " for t in texts]
        try:
            raw = await asyncio.gather(
                *(self._embed_one(t) for t in cleaned), return_exceptions=True
            )
            first_error = next((r for r in raw if isinstance(r, BaseException)), None)
            if first_error is not None:
                raise first_error
            vectors: list[list[float]] = list(raw)  # type: ignore[arg-type]
            self._validate(vectors)
            logger.debug(
                "embedding 完成", model=model, texts=len(texts), dim=len(vectors[0]) if vectors else 0
            )
            return vectors
        finally:
            # 成功与失败路径都落审计（失败已 append 过 ERROR 日志）
            if auto_flush:
                await self.flush_logs()

    async def _embed_one(self, text: str) -> list[float]:
        """单条 embedding 请求（受并发闸门 + 可选 QPS 令牌桶限流）。"""
        async with self._sem:
            if self._rate_limiter is not None:
                await self._rate_limiter.acquire()
            return await self._request(text)

    async def _request(self, text: str) -> list[float]:
        url, headers = self._endpoint()
        model = self.settings.model_for(self.settings.embedding_provider, "embedding")
        started = time.perf_counter()
        max_retries = max(0, int(self.settings.embedding_max_retries))
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                resp = await self.client.post(url, json=self._payload(text), headers=headers)
                resp.raise_for_status()
                data = resp.json()
                vec = self._parse_response(data)
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                # 429 与 5xx 是瞬时/服务端问题，退避后重试；4xx 其它错误不重试
                if (status == 429 or status >= 500) and attempt < max_retries:
                    delay = self._retry_delay(exc.response, attempt)
                    logger.warning(
                        "embedding 限流/服务端错误，退避重试",
                        attempt=attempt + 1,
                        status=status,
                        delay_ms=int(delay * 1000),
                    )
                    await asyncio.sleep(delay)
                    continue
                break
            except Exception as exc:  # noqa: BLE001 - 网络/解析错误不重试，统一收口
                last_error = exc
                break

            usage = data.get("usage") or {}
            prompt_tokens = int(usage.get("input_tokens") or usage.get("total_tokens") or 0)
            latency_ms = int((time.perf_counter() - started) * 1000)
            self._log_call(
                model=model, prompt_tokens=prompt_tokens, latency_ms=latency_ms, status="OK"
            )
            return vec

        # 重试耗尽或不可重试错误：记录一次失败日志后抛出
        latency_ms = int((time.perf_counter() - started) * 1000)
        self._log_call(
            model=model, prompt_tokens=0, latency_ms=latency_ms,
            status="ERROR", error=str(last_error)[:500],
        )
        assert last_error is not None  # 循环内所有退出路径都已赋值
        raise last_error

    @staticmethod
    def _retry_delay(resp: httpx.Response, attempt: int) -> float:
        """优先取 Retry-After 头，否则指数退避（1s/2s/4s…封顶 30s），加抖动避免惊群。"""
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return min(2 ** attempt, 30) + random.uniform(0, 1)

    def _log_call(
        self,
        *,
        model: str,
        prompt_tokens: int,
        latency_ms: int,
        status: str,
        error: str | None = None,
    ) -> None:
        """向进程内队列追加一条 embedding 审计（不立即写库）。"""
        self._pending_logs.append(
            LLMCallLog(
                trace_id=uuid.uuid4().hex[:16],
                run_id=current_run_id(),
                provider=self.settings.embedding_provider,
                role="embedding",
                model=model,
                is_fallback=False,
                request_chars=0,
                prompt_tokens=prompt_tokens,
                completion_tokens=0,
                latency_ms=latency_ms,
                status=status,
                error_message=error,
                # embedding 只有输入 token，输出按 0 计
                cost_cent=calc_cost_cent(model, prompt_tokens, 0),
            )
        )

    async def flush_logs(self) -> None:
        """把攒批的审计日志一次性写库。

        幂等；写库失败只告警（与旧版语义一致，审计不能阻断向量化主流程）。
        并发 flush 时由锁串行化，先到者取走全部 pending，后到者发现为空即返回。
        """
        async with self._flush_lock:
            if not self._pending_logs:
                return
            logs = self._pending_logs
            self._pending_logs = []
        try:
            async with session_scope() as session:
                session.add_all(logs)
        except Exception as exc:  # noqa: BLE001 - 审计失败不能影响主流程
            logger.warning("批量写入 embedding 审计日志失败", count=len(logs), error=str(exc)[:200])

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> list[float]:
        """从响应中提取向量。data 字段是对象 {"embedding": [...]} 而非数组。"""
        return data["data"]["embedding"]

    async def embed_one(self, text: str) -> list[float]:
        """单条向量（检索/QA 查询路径）。审计即时写，行为与旧版一致。"""
        vecs = await self.embed([text], auto_flush=True)
        return vecs[0] if vecs else []

    # ------------------------------------------------------------------
    def _validate(self, vectors: list[list[float]]) -> None:
        expected = self.settings.embedding_dim
        for vec in vectors:
            if len(vec) != expected:
                raise DimensionMismatch(
                    f"embedding 维度不匹配：期望 {expected}，实际 {len(vec)}。"
                    + "请检查 EMBEDDING_DIM 与请求的 dimensions 参数是否一致（doubao-embedding-vision 支持 1024/2048）。"
                )


_embedder: Embedder | None = None


def get_embedder(settings: Settings | None = None) -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder(settings)
    return _embedder
