"""DeepAgents 执行器：统一装配模型、工具与超时，并在不可用时降级为单次结构化调用。"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from fin_news.agents.llm import get_llm_client, get_semaphore, parse_json_content
from fin_news.agents.prompts import ANALYSIS_SCHEMA
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import AgentType
from fin_news.core.logging import get_logger

logger = get_logger("agents.base")


@dataclass
class AgentOutput:
    data: dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    degraded: bool = False


class AgentExecutionError(RuntimeError):
    pass


def _build_chat_model(role: str = "analysis", settings: Settings | None = None):
    """构造 LangChain ChatModel（OpenAI 兼容，火山引擎 / DeepSeek 均可）。"""
    from langchain_openai import ChatOpenAI

    from fin_news.agents.llm.callbacks import AuditCallbackHandler

    settings = settings or get_settings()
    provider = settings.llm_default_provider
    cfg = settings.provider(provider)  # type: ignore[arg-type]
    model = settings.model_for(provider, role)  # type: ignore[arg-type]
    return ChatOpenAI(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        model=model,
        temperature=0.2,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        callbacks=[AuditCallbackHandler(role=role, provider=provider, model=model)],
    )


async def _run_deep_agent(
    agent_type: AgentType,
    system_prompt: str,
    user_prompt: str,
    tools: Sequence[Any],
    settings: Settings,
) -> AgentOutput:
    from deepagents import create_deep_agent

    started = time.perf_counter()
    model = _build_chat_model("analysis", settings)
    agent = create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt,
        name=f"fin-news-{agent_type.value}",
    )
    result = await asyncio.wait_for(
        agent.ainvoke({"messages": [{"role": "user", "content": user_prompt}]}),
        timeout=settings.analysis_timeout_seconds,
    )
    messages = (result or {}).get("messages") or []
    content = ""
    for msg in reversed(messages):
        text = getattr(msg, "content", None)
        if isinstance(text, str) and text.strip():
            content = text
            break
        if isinstance(text, list):
            parts = [p.get("text", "") for p in text if isinstance(p, dict) and p.get("type") == "text"]
            if parts:
                content = "".join(parts)
                break

    data = parse_json_content(content) or {}
    if not isinstance(data, dict):
        data = {"headline": "", "summary": content[:500], "bullets": []}
    usage = _extract_usage(result)
    return AgentOutput(
        data=data,
        raw=content,
        model=settings.model_for(settings.llm_default_provider, "analysis"),  # type: ignore[arg-type]
        prompt_tokens=usage[0],
        completion_tokens=usage[1],
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def _extract_usage(result: Any) -> tuple[int, int]:
    """尽力从 LangGraph 结果中提取 token 用量（不同版本结构不同，取不到就记 0）。"""
    try:
        messages = (result or {}).get("messages") or []
        for msg in reversed(messages):
            meta = getattr(msg, "response_metadata", None) or {}
            usage = meta.get("token_usage") or meta.get("usage") or {}
            if usage:
                return int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)
    except Exception:  # noqa: BLE001
        pass
    return 0, 0


async def _run_plain_agent(
    system_prompt: str,
    user_prompt: str,
    settings: Settings,
    *,
    response_schema: dict[str, Any] = ANALYSIS_SCHEMA,
) -> AgentOutput:
    """降级路径：直接一次结构化调用（工具结果已在 user_prompt 中内联）。"""
    client = get_llm_client(settings)
    resp = await client.chat(
        role="analysis",
        system=system_prompt,
        user=user_prompt,
        response_schema=response_schema,
        temperature=0.2,
    )
    data = resp.data if isinstance(resp.data, dict) else {}
    if not data:
        data = {"headline": "", "summary": (resp.content or "")[:500], "bullets": []}
    return AgentOutput(
        data=data,
        raw=resp.content,
        model=resp.model,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        latency_ms=resp.latency_ms,
        degraded=True,
    )


async def run_agent(
    agent_type: AgentType,
    system_prompt: str,
    user_prompt: str,
    *,
    tools: Sequence[Any] | None = None,
    settings: Settings | None = None,
) -> AgentOutput:
    """执行分析 Agent：优先 DeepAgents，异常时降级为单次结构化调用。"""
    settings = settings or get_settings()
    semaphore = get_semaphore("analysis", settings)
    tools = list(tools or [])

    async with semaphore:
        if settings.use_deep_agents:
            try:
                return await _run_deep_agent(agent_type, system_prompt, user_prompt, tools, settings)
            except TimeoutError:
                logger.warning("Agent 超时，降级为单次调用", agent=agent_type.value)
            except ImportError:
                logger.warning("deepagents 不可用，降级为单次调用", agent=agent_type.value)
            except Exception as exc:  # noqa: BLE001
                logger.warning("DeepAgents 执行失败，降级", agent=agent_type.value, error=str(exc)[:300])
        return await _run_plain_agent(system_prompt, user_prompt, settings)
