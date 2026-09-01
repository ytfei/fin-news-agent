"""分析 Agent 的 DeepAgents 图：按需构建 + 缓存 + Pydantic 结构化输出。

框架分工（见 docs/05-agent-refactor-design.md）：
* 评分 → LangGraph 显式图（scoring_graph.py）：高频低延迟、流程固定
* 宏观 / 行业 / 个股 → DeepAgents：开放式分析，需要自主规划 + 多步工具调用

关键改造（相对旧版 agents/base.py）：
1. 图按 (agent_type, prompt_version) 缓存，不再每条资讯重建
2. response_format=AnalysisPayload（Pydantic），原生结构化输出，不再靠正则捞 JSON
3. 宏观 Agent 用子 agent 并行（历史 / 传导 / 外部），主 agent 只做汇总
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from deepagents.middleware.subagents import SubAgent

from fin_news.agents.llm.factory import get_model_factory
from fin_news.agents.prompts import (
    INDUSTRY_SYSTEM,
    INDUSTRY_VERSION,
    MACRO_SYSTEM,
    MACRO_VERSION,
    STOCK_SYSTEM,
    STOCK_VERSION,
)
from fin_news.agents.schemas import AnalysisPayload
from fin_news.agents.tools.langchain_tools import (
    history_search as history_search_tool,
    market_snapshot as market_snapshot_tool,
    stock_lookup as stock_lookup_tool,
    web_search as web_search_tool,
)
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import AgentType
from fin_news.core.logging import get_logger

logger = get_logger("agents.graphs.analysis")


@dataclass
class AnalysisRun:
    """一次分析图的执行结果。payload=None 表示结构化输出失败（调用方负责降级）。"""

    payload: AnalysisPayload | None = None
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None


# (agent_type -> system_prompt, prompt_version) 与 analysis_agents.AGENT_CONFIG 对齐
AGENT_GRAPH_CONFIG: dict[AgentType, tuple[str, str]] = {
    AgentType.MACRO_POLICY: (MACRO_SYSTEM, MACRO_VERSION),
    AgentType.INDUSTRY: (INDUSTRY_SYSTEM, INDUSTRY_VERSION),
    AgentType.STOCK: (STOCK_SYSTEM, STOCK_VERSION),
}


# ----------------------------------------------------------------------
# 子 Agent（仅宏观）：历史 / 传导 / 外部 三者并行，主 agent 汇总
# ----------------------------------------------------------------------


def _macro_subagents(settings: Settings) -> list[SubAgent]:
    """宏观 Agent 的子 agent。web_search 未配置时不挂 external-analyst。"""
    subs: list[SubAgent] = [
        SubAgent(
            name="history-analyst",
            description="检索历史同类宏观/政策事件，对比政策力度与当时的市场反应。",
            system_prompt=(
                "你是历史事件对比分析师。用 history_search 检索历史上与当前宏观/政策事件最相似的资讯，"
                "总结三点：1) 当时政策力度；2) 市场短期与中期反应；3) 传导时滞。"
                "输出精炼的中文要点，供主分析师汇总。"
            ),
            tools=[history_search_tool],
        ),
        SubAgent(
            name="transmission-analyst",
            description="用 market_snapshot / stock_lookup 推演流动性、风险偏好与板块传导路径。",
            system_prompt=(
                "你是传导路径分析师。用 market_snapshot 获取当前市场状态，必要时用 stock_lookup 查关键标的，"
                "推演该事件如何经由流动性、风险偏好、行业景气传导到具体板块。"
                "输出受益板块与受损板块及传导时滞，供主分析师汇总。"
            ),
            tools=[market_snapshot_tool, stock_lookup_tool],
        ),
    ]
    if settings.web_search_enabled:
        subs.append(
            SubAgent(
                name="external-analyst",
                description="用 web_search 检索外部公开信息、市场预期与海外反应，过滤可信来源。",
                system_prompt=(
                    "你是外部信息分析师。用 web_search 检索该事件的机构解读、市场预期、海外反应与相关资产表现，"
                    "只保留可信来源，标注信息时点。输出精炼的中文要点，供主分析师汇总。"
                ),
                tools=[web_search_tool],
            )
        )
    return subs


# ----------------------------------------------------------------------
# 图构建与缓存
# ----------------------------------------------------------------------


def _response_format_for(settings: Settings) -> Any:
    """按 provider 选择结构化输出策略。

    实测（2026-09-01）：
    * 火山引擎：json_schema（ProviderStrategy）可用，且质量优于 function_calling
    * DeepSeek：不支持 `response_format=json_schema`（返回 400 "This response_format
      type is unavailable now"），只能走 function_calling（ToolStrategy）
    """
    from langchain.agents.structured_output import ProviderStrategy, ToolStrategy

    if settings.llm_default_provider == "deepseek":
        return ToolStrategy(AnalysisPayload)
    return ProviderStrategy(AnalysisPayload)


def build_analysis_graph(
    agent_type: AgentType, settings: Settings | None = None, *, response_format: Any = None
):
    """构建 DeepAgents 图。response_format 可注入（测试用），默认按 provider 选择。"""
    from deepagents import create_deep_agent

    settings = settings or get_settings()
    system_prompt, _version = AGENT_GRAPH_CONFIG[agent_type]

    # 注意：必须用 with_fallback=False（纯 ChatOpenAI）。deepagents 的 resolve_model
    # 只认识 BaseChatModel / str，`with_fallbacks()` 返回的 RunnableWithFallbacks 会被
    # 误当成字符串去 `spec.count(":")`，直接 AttributeError。主备降级改由
    # analysis_agents._run_analysis 在「整图失败」时降级到 legacy 单次调用（带主备降级）。
    model = get_model_factory(settings).chat("analysis", with_fallback=False)
    tools = _main_tools(agent_type, settings)
    subagents = _macro_subagents(settings) if agent_type == AgentType.MACRO_POLICY else None

    rf = response_format if response_format is not None else _response_format_for(settings)
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        subagents=subagents,
        response_format=rf,
        name=f"fin-news-{agent_type.value}",
    )


def _main_tools(agent_type: AgentType, settings: Settings) -> list[Any]:
    """主 agent 的工具集。

    历史检索 / 个股估值 / 市场快照都交给 Agent 按需调用（不再预取塞进 prompt），
    宏观额外开放 web_search。与旧版 build_toolset 保持一致，但去掉预取重复。
    """
    tools: list[Any] = [history_search_tool, stock_lookup_tool, market_snapshot_tool]
    if agent_type == AgentType.MACRO_POLICY and settings.web_search_enabled:
        tools.insert(1, web_search_tool)
    return tools


_graphs: dict[tuple[AgentType, str, str, str], Any] = {}


def get_analysis_graph(agent_type: AgentType, settings: Settings | None = None) -> Any:
    """按 (agent_type, prompt_version, provider, model) 缓存已编译的图。"""
    settings = settings or get_settings()
    _system_prompt, version = AGENT_GRAPH_CONFIG[agent_type]
    key = (
        agent_type,
        version,
        settings.llm_default_provider,
        settings.model_for(settings.llm_default_provider, "analysis"),
    )
    if key not in _graphs:
        _graphs[key] = build_analysis_graph(agent_type, settings)
        logger.info("构建分析 Agent 图", agent=agent_type.value, version=version)
    return _graphs[key]


def clear_cache() -> None:
    _graphs.clear()


# ----------------------------------------------------------------------
# 执行与结果提取
# ----------------------------------------------------------------------


def _usage_of(result: dict[str, Any]) -> tuple[int, int]:
    """累计所有 AIMessage 的 token 用量（DeepAgents 多步调用会产出多条）。"""
    prompt = completion = 0
    for msg in result.get("messages") or []:
        meta = getattr(msg, "usage_metadata", None) or {}
        prompt += int(meta.get("input_tokens") or 0)
        completion += int(meta.get("output_tokens") or 0)
    return prompt, completion


def _model_of(result: dict[str, Any]) -> str:
    for msg in reversed(result.get("messages") or []):
        meta = getattr(msg, "response_metadata", None) or {}
        name = meta.get("model_name") or meta.get("model")
        if name:
            return str(name)
    return ""


async def run_analysis(
    agent_type: AgentType,
    user_prompt: str,
    settings: Settings | None = None,
    *,
    graph: Any | None = None,
) -> AnalysisRun:
    """执行一次分析图，返回结构化结果（payload 为 AnalysisPayload 或 None）。"""
    settings = settings or get_settings()
    started = time.perf_counter()
    compiled = graph if graph is not None else get_analysis_graph(agent_type, settings)

    try:
        result = await asyncio.wait_for(
            compiled.ainvoke({"messages": [HumanMessage(content=user_prompt)]}),
            timeout=settings.analysis_timeout_seconds,
        )
    except TimeoutError:
        return AnalysisRun(latency_ms=int((time.perf_counter() - started) * 1000), error="timeout")
    except Exception as exc:  # noqa: BLE001 - 图执行失败（含结构化输出不支持）
        return AnalysisRun(
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
        )

    payload = result.get("structured_response")
    if payload is not None and not isinstance(payload, AnalysisPayload):
        # 极端情况下可能拿到 dict / 其他类型，做一次归一化
        try:
            payload = AnalysisPayload.model_validate(payload)
        except Exception:  # noqa: BLE001
            payload = None

    prompt_tokens, completion_tokens = _usage_of(result)
    return AnalysisRun(
        payload=payload,
        model=_model_of(result),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
