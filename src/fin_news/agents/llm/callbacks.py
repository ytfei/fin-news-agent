"""LangChain 回调：把模型调用统一写入 llm_call_log（沿用原有审计表）。

替代原先靠 `_extract_usage` 猜测 token 的做法：优先取 LangChain 标准的
`usage_metadata`，取不到时按字符粗估并标记 estimated=true。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

from fin_news.core.db import session_scope
from fin_news.core.logging import get_logger
from fin_news.models.event import LLMCallLog

logger = get_logger("agents.llm.callbacks")

# 粗略成本估算（分/千 token），与 client.py 保持一致
_PRICE_PER_1K_CENT = {
    "scoring": 0.05,
    "analysis": 0.6,
    "qa": 0.6,
    "embedding": 0.01,
}


class AuditCallbackHandler(AsyncCallbackHandler):
    """审计回调：一次 LLM 调用一行 llm_call_log。"""

    def __init__(
        self,
        role: str = "analysis",
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
        model: str = "",
    ) -> None:
        self.role = role
        self.run_id = run_id
        self.trace_id = trace_id or uuid.uuid4().hex[:16]
        self.model = model
        self._started: dict[str, float] = {}

    # ------------------------------------------------------------------
    async def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        self._started[run_id] = time.perf_counter()
        model = (serialized or {}).get("kwargs", {}).get("model") or self.model
        if model:
            self.model = model

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        started = self._started.pop(run_id, None)
        latency_ms = int((time.perf_counter() - started) * 1000) if started else 0
        prompt_tokens, completion_tokens, estimated = _extract_usage(response)
        await self._log(
            provider=_provider_of(response),
            model=self.model,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated=estimated,
            status="OK",
        )

    async def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id") or "")
        started = self._started.pop(run_id, None)
        latency_ms = int((time.perf_counter() - started) * 1000) if started else 0
        await self._log(
            provider="",
            model=self.model,
            latency_ms=latency_ms,
            prompt_tokens=0,
            completion_tokens=0,
            estimated=True,
            status="ERROR",
            error=str(error)[:500],
        )

    # ------------------------------------------------------------------
    async def _log(
        self,
        *,
        provider: str,
        model: str,
        latency_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
        estimated: bool,
        status: str,
        error: str | None = None,
    ) -> None:
        rate = _PRICE_PER_1K_CENT.get(self.role, 0.5)
        cost = round((prompt_tokens + completion_tokens) / 1000 * rate, 4)
        try:
            async with session_scope() as session:
                session.add(
                    LLMCallLog(
                        trace_id=self.trace_id,
                        run_id=self.run_id,
                        provider=provider,
                        role=self.role,
                        model=model,
                        is_fallback=False,
                        request_chars=0,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        latency_ms=latency_ms,
                        status=status,
                        error_message=error,
                        cost_cent=cost,
                        estimated=estimated,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - 审计失败不能影响主流程
            logger.warning("写入模型调用日志失败", error=str(exc)[:200])


def _extract_usage(response: LLMResult) -> tuple[int, int, bool]:
    """返回 (prompt_tokens, completion_tokens, 是否为估算值)。"""
    # 1) LangChain 标准字段
    try:
        usage = getattr(response, "usage_metadata", None) or {}
        if usage:
            return (
                int(usage.get("input_tokens") or 0),
                int(usage.get("output_tokens") or 0),
                False,
            )
    except Exception:  # noqa: BLE001
        pass

    # 2) 生成结果里的 AIMessage
    try:
        msg = response.generations[0][0].message
        meta = getattr(msg, "usage_metadata", None) or {}
        if meta:
            return (
                int(meta.get("input_tokens") or 0),
                int(meta.get("output_tokens") or 0),
                False,
            )
        rm = getattr(msg, "response_metadata", None) or {}
        for key in ("token_usage", "usage"):
            raw = rm.get(key) or {}
            if raw:
                return (
                    int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0),
                    int(raw.get("completion_tokens") or raw.get("output_tokens") or 0),
                    False,
                )
    except Exception:  # noqa: BLE001
        pass

    # 3) 兜底：按字符粗估（中文约 1.5 字符/token）
    try:
        text = response.generations[0][0].text or ""
        return int(len(text) / 1.5), 0, True
    except Exception:  # noqa: BLE001
        return 0, 0, True


def _provider_of(response: LLMResult) -> str:
    """尽力从 response_metadata 推断 provider（失败不影响审计）。"""
    try:
        msg = response.generations[0][0].message
        meta = getattr(msg, "response_metadata", None) or {}
        name = str(meta.get("model_provider") or meta.get("provider") or "")
        if name:
            return name
        model_name = str(meta.get("model_name") or meta.get("model") or "")
        if "deepseek" in model_name:
            return "deepseek"
        if model_name:
            return "volcengine"
    except Exception:  # noqa: BLE001
        pass
    return ""
