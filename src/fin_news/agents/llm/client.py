"""LLM 调用封装（OpenAI 兼容协议，火山引擎 / DeepSeek 自由切换）。

- 角色化选型：scoring(轻量) / analysis(强推理) / qa / embedding
- 每个角色有主 provider 与备 provider，主失败自动降级
- JSON 输出模式 + schema 提示 + 容错解析
- 每次调用写入 llm_call_log（审计与成本）
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from openai import APIConnectionError, APIError, AsyncOpenAI, RateLimitError

from fin_news.core.config import Settings, get_settings
from fin_news.core.db import session_scope
from fin_news.core.logging import get_logger
from fin_news.models.event import LLMCallLog

logger = get_logger("agents.llm")

Role = Literal["scoring", "analysis", "qa", "embedding"]

_JSON_BLOCK_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


class LLMUnavailable(RuntimeError):
    """主备模型均不可用。"""


@dataclass
class ChatResult:
    content: str
    data: dict[str, Any] | list[Any] | None = None
    model: str = ""
    provider: str = ""
    is_fallback: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


def parse_json_content(content: str) -> dict[str, Any] | list[Any] | None:
    """容错解析模型输出（兼容 ```json 代码块、前后缀说明文字）。"""
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


class LLMClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._clients: dict[str, AsyncOpenAI] = {}

    def _client(self, provider: str) -> AsyncOpenAI:
        if provider not in self._clients:
            cfg = self.settings.provider(provider)  # type: ignore[arg-type]
            if not cfg.api_key:
                raise LLMUnavailable(f"provider {provider} 未配置 api_key")
            self._clients[provider] = AsyncOpenAI(
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                timeout=self.settings.llm_timeout_seconds,
                max_retries=self.settings.llm_max_retries,
            )
        return self._clients[provider]

    def _providers_for(self, role: Role) -> list[str]:
        primary = self.settings.llm_default_provider
        fallback = self.settings.llm_fallback_provider
        return [primary, fallback] if fallback and fallback != primary else [primary]

    # ------------------------------------------------------------------
    async def chat(
        self,
        role: Role,
        system: str,
        user: str,
        *,
        json_mode: bool = True,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
    ) -> ChatResult:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        if response_schema:
            user = (
                f"{user}\n\n请严格按以下 JSON Schema 输出，且只输出 JSON，不要任何解释：\n"
                f"```json\n{json.dumps(response_schema, ensure_ascii=False, indent=2)}\n```"
            )
        elif json_mode:
            user = f"{user}\n\n请只输出合法的 JSON，不要包含任何解释文字或代码块标记。"
        messages.append({"role": "user", "content": user})

        last_error: Exception | None = None
        for idx, provider in enumerate(self._providers_for(role)):
            is_fallback = idx > 0
            model = self.settings.model_for(provider, role)  # type: ignore[arg-type]
            started = time.perf_counter()
            try:
                client = self._client(provider)
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if max_tokens:
                    kwargs["max_tokens"] = max_tokens
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}

                resp = await client.chat.completions.create(**kwargs)
                content = resp.choices[0].message.content or ""
                usage = getattr(resp, "usage", None)
                result = ChatResult(
                    content=content,
                    data=parse_json_content(content) if json_mode else None,
                    model=model,
                    provider=provider,
                    is_fallback=is_fallback,
                    prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                    completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
                await self._log_call(
                    role=role,
                    result=result,
                    request_chars=len(system) + len(user),
                    status="OK",
                    run_id=run_id,
                    trace_id=trace_id,
                )
                return result
            except (APIConnectionError, RateLimitError, APIError, LLMUnavailable) as exc:
                last_error = exc
                latency = int((time.perf_counter() - started) * 1000)
                await self._log_call(
                    role=role,
                    result=ChatResult(content="", model=model, provider=provider, is_fallback=is_fallback, latency_ms=latency),
                    request_chars=len(system) + len(user),
                    status="ERROR",
                    error=str(exc)[:500],
                    run_id=run_id,
                    trace_id=trace_id,
                )
                logger.warning(
                    "模型调用失败，准备降级",
                    role=role,
                    provider=provider,
                    model=model,
                    error=str(exc)[:200],
                )
                continue
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.exception("模型调用异常", role=role, provider=provider, error=str(exc))
                break

        raise LLMUnavailable(f"角色 {role} 的所有 provider 均失败: {last_error}")

    # ------------------------------------------------------------------
    async def chat_stream(
        self,
        role: Role,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        run_id: str | None = None,
        trace_id: str | None = None,
    ):
        """流式输出（用于追问）。逐个 yield 文本片段，主 provider 失败时自动切备 provider。"""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        last_error: Exception | None = None
        for idx, provider in enumerate(self._providers_for(role)):
            model = self.settings.model_for(provider, role)  # type: ignore[arg-type]
            started = time.perf_counter()
            buffer: list[str] = []
            try:
                client = self._client(provider)
                stream = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    stream=True,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        buffer.append(delta)
                        yield delta
                await self._log_call(
                    role=role,
                    result=ChatResult(
                        content="".join(buffer),
                        model=model,
                        provider=provider,
                        is_fallback=idx > 0,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    ),
                    request_chars=len(system) + len(user),
                    status="OK",
                    run_id=run_id,
                    trace_id=trace_id,
                )
                return
            except (APIConnectionError, RateLimitError, APIError, LLMUnavailable) as exc:
                last_error = exc
                await self._log_call(
                    role=role,
                    result=ChatResult(content="", model=model, provider=provider, is_fallback=idx > 0,
                                      latency_ms=int((time.perf_counter() - started) * 1000)),
                    request_chars=len(system) + len(user),
                    status="ERROR",
                    error=str(exc)[:500],
                    run_id=run_id,
                    trace_id=trace_id,
                )
                if buffer:
                    # 已输出部分内容，不重试，避免前端出现重复文本
                    raise LLMUnavailable(str(exc)) from exc
                logger.warning("流式调用失败，准备降级", provider=provider, error=str(exc)[:200])
                continue
        raise LLMUnavailable(f"角色 {role} 的所有 provider 均失败: {last_error}")

    # ------------------------------------------------------------------
    async def _log_call(
        self,
        *,
        role: str,
        result: ChatResult,
        request_chars: int,
        status: str,
        error: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        try:
            async with session_scope() as session:  # type: AsyncSession
                session.add(
                    LLMCallLog(
                        trace_id=trace_id or uuid.uuid4().hex[:16],
                        run_id=run_id,
                        provider=result.provider,
                        role=role,
                        model=result.model,
                        is_fallback=result.is_fallback,
                        request_chars=request_chars,
                        prompt_tokens=result.prompt_tokens,
                        completion_tokens=result.completion_tokens,
                        latency_ms=result.latency_ms,
                        status=status,
                        error_message=error,
                        cost_cent=_estimate_cost_cent(role, result),
                    )
                )
        except Exception as exc:  # noqa: BLE001 - 审计失败不影响主流程
            logger.warning("写入模型调用日志失败", error=str(exc)[:200])


# 粗略成本估算（分）；按 provider 单价表配置即可，这里按量级估算
_PRICE_PER_1K_CENT = {
    "scoring": 0.05,
    "analysis": 0.6,
    "qa": 0.6,
    "embedding": 0.01,
}


def _estimate_cost_cent(role: str, result: ChatResult) -> float:
    rate = _PRICE_PER_1K_CENT.get(role, 0.5)
    tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
    return round(tokens / 1000 * rate, 4)


_client: LLMClient | None = None


def get_llm_client(settings: Settings | None = None) -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient(settings)
    return _client
