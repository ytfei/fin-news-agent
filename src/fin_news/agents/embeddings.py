"""Embedding 客户端：直连火山方舟多模态向量化接口。

doubao-embedding-vision 与旧文本模型（doubao-embedding-text-240715）的关键差异：

* 接口：`POST {base_url}/embeddings/multimodal`（不是 OpenAI 兼容的 `/embeddings`）
* input 为**对象数组** `[{"type": "text", "text": "..."}]`，不是字符串数组
* 维度由请求参数 `dimensions` 指定（1024 或 2048），不再是模型固定值
* **单样本语义**：一次请求的 input 数组表示「一个多模态样本的若干部分」
  （文本/图片/视频混合），返回的是这一个样本的**一个**融合向量 —— 响应 `data`
  字段是对象 `{"embedding": [...]}` 而非 OpenAI 的数组 `[{"embedding": ...}]`。
  因此无法像 OpenAI 那样一次请求批量文本，必须逐条请求（这里用 asyncio.gather
  并发逐条，分批限流）。

保留自建 Embedder 的原因：
* 写入前的维度校验（维度不一致必须终止，否则污染向量索引）
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx

from fin_news.agents.llm.client import LLMUnavailable
from fin_news.core.config import Settings, get_settings
from fin_news.core.db import session_scope
from fin_news.core.logging import get_logger
from fin_news.models.event import LLMCallLog

logger = get_logger("agents.embedding")

# 粗略成本估算（分/千 token），与 llm/callbacks.py 的 embedding 单价一致
_EMBEDDING_PRICE_PER_1K_CENT = 0.01


class DimensionMismatch(ValueError):
    """向量维度与配置不一致（禁止写入，避免污染索引）。"""


class Embedder:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    @property
    def client(self) -> httpx.AsyncClient:
        """复用 httpx.AsyncClient（embedding 调用频繁，避免每次建连）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds)
        return self._client

    async def close(self) -> None:
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
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self.settings.model_for(self.settings.embedding_provider, "embedding")
        vectors: list[list[float]] = []
        batch_size = max(1, self.settings.embedding_batch_size)

        # 单样本语义：逐条请求，用 gather 分批并发限流
        for i in range(0, len(texts), batch_size):
            batch = [t.replace("\n", " ").strip() or " " for t in texts[i : i + batch_size]]
            vectors.extend(await asyncio.gather(*(self._embed_one(t) for t in batch)))

        self._validate(vectors)
        logger.debug("embedding 完成", model=model, texts=len(texts), dim=len(vectors[0]) if vectors else 0)
        return vectors

    async def _embed_one(self, text: str) -> list[float]:
        url, headers = self._endpoint()
        model = self.settings.model_for(self.settings.embedding_provider, "embedding")
        started = time.perf_counter()
        try:
            resp = await self.client.post(url, json=self._payload(text), headers=headers)
            resp.raise_for_status()
            data = resp.json()
            vec = self._parse_response(data)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - started) * 1000)
            await self._log_call(model=model, prompt_tokens=0, latency_ms=latency_ms,
                                 status="ERROR", error=str(exc)[:500])
            raise

        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("input_tokens") or usage.get("total_tokens") or 0)
        latency_ms = int((time.perf_counter() - started) * 1000)
        await self._log_call(model=model, prompt_tokens=prompt_tokens, latency_ms=latency_ms,
                             status="OK")
        return vec

    async def _log_call(
        self,
        *,
        model: str,
        prompt_tokens: int,
        latency_ms: int,
        status: str,
        error: str | None = None,
    ) -> None:
        """写一次 embedding 调用到 llm_call_log（审计与成本）。"""
        try:
            async with session_scope() as session:
                session.add(
                    LLMCallLog(
                        trace_id=uuid.uuid4().hex[:16],
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
                        cost_cent=round(prompt_tokens / 1000 * _EMBEDDING_PRICE_PER_1K_CENT, 4),
                    )
                )
        except Exception as exc:  # noqa: BLE001 - 审计失败不能影响主流程
            logger.warning("写入 embedding 调用日志失败", error=str(exc)[:200])

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> list[float]:
        """从响应中提取向量。data 字段是对象 {"embedding": [...]} 而非数组。"""
        return data["data"]["embedding"]

    async def embed_one(self, text: str) -> list[float]:
        vecs = await self.embed([text])
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
