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

from deepagents.middleware.subagents import SubAgent
from langchain_core.messages import HumanMessage

from fin_news.agents.llm.factory import get_model_factory
from fin_news.agents.prompts import (
    INDUSTRY_SYSTEM,
    INDUSTRY_VERSION,
    MACRO_SYSTEM,
    MACRO_VERSION,
    POST_MARKET_SYSTEM,
    POST_MARKET_VERSION,
    PRE_MARKET_SYSTEM,
    PRE_MARKET_VERSION,
    STOCK_SYSTEM,
    STOCK_VERSION,
)
from fin_news.agents.schemas import AnalysisPayload
from fin_news.agents.tools.langchain_tools import (
    history_search as history_search_tool,
)
from fin_news.agents.tools.langchain_tools import (
    market_snapshot as market_snapshot_tool,
)
from fin_news.agents.tools.langchain_tools import (
    stock_lookup as stock_lookup_tool,
)
from fin_news.agents.tools.langchain_tools import (
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
    # 盘前/盘后：上下文由 market_agents._build_context 预取后内联进 prompt，
    # 输出同样收敛到 AnalysisPayload（verdict/attribution 等走 extras）
    AgentType.PRE_MARKET: (PRE_MARKET_SYSTEM, PRE_MARKET_VERSION),
    AgentType.POST_MARKET: (POST_MARKET_SYSTEM, POST_MARKET_VERSION),
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


def _pre_market_subagents(settings: Settings) -> list[SubAgent]:
    """盘前子 agent：隔夜外盘 / 消息面 / 盘面预判 三路并行。

    us_daily 无权限，隔夜外盘只能靠外部搜索补齐，故 overnight-analyst
    仅在搜索可用时挂载（与宏观的 external-analyst 同一策略）。
    """
    subs: list[SubAgent] = [
        SubAgent(
            name="newsflow-analyst",
            description="检索隔夜高评分资讯，识别真正影响今日开盘的主线与消息强弱。",
            system_prompt=(
                "你是消息面分析师。用 history_search 检索隔夜至开盘前的财经资讯，"
                "剔除重复转载与噪声，归纳出真正影响今日开盘的 3-8 条主线，"
                "每条标注影响方向（利好/利空）与强度。"
                "输出精炼的中文要点，供主分析师汇总。"
            ),
            tools=[history_search_tool],
        ),
        SubAgent(
            name="positioning-analyst",
            description="用 market_snapshot / stock_lookup 分析最近收盘状态，预判今日开盘与主线方向。",
            system_prompt=(
                "你是盘面预判分析师。用 market_snapshot 获取最近交易日的市场状态"
                "（指数、涨跌家数、成交额、板块涨跌），必要时用 stock_lookup 查关键权重股，"
                "判断今日大概率的开盘状态（高开/低开/平开）、可能延续或反转的方向，"
                "以及需要规避的方向。输出精炼的中文要点，供主分析师汇总。"
            ),
            tools=[market_snapshot_tool, stock_lookup_tool],
        ),
    ]
    if settings.web_search_enabled:
        subs.insert(
            0,
            SubAgent(
                name="overnight-analyst",
                description="检索隔夜美股、欧股与大宗商品表现，补齐 us_daily 无权限的外盘数据缺口。",
                system_prompt=(
                    "你是隔夜外盘分析师。用 web_search 检索隔夜美股三大指数、欧洲主要股指、"
                    "关键大宗商品（原油、黄金、有色）与中概股表现。"
                    "只采用可信来源并标注数据时点；检索不到某项要明确说明缺失，禁止编造数值。"
                    "输出各市场涨跌幅与对 A 股的映射方向，供主分析师汇总。"
                ),
                tools=[web_search_tool],
            ),
        )
    return subs


def _post_market_subagents(settings: Settings) -> list[SubAgent]:
    """盘后子 agent：盘面复盘 / 归因 / 次日展望 三路并行。"""
    subs: list[SubAgent] = [
        SubAgent(
            name="tape-analyst",
            description="用 market_snapshot / stock_lookup 复盘当日涨跌结构、板块轮动与资金去向。",
            system_prompt=(
                "你是盘面复盘分析师。用 market_snapshot 获取当日市场快照"
                "（指数、涨跌家数、成交额、涨停跌停、板块涨跌 TOP/BOTTOM），"
                "必要时用 stock_lookup 查领涨领跌个股，"
                "判断今天是普涨、结构市还是普跌，资金在往哪些板块去、从哪些板块撤。"
                "输出精炼的中文要点，供主分析师汇总。"
            ),
            tools=[market_snapshot_tool, stock_lookup_tool],
        ),
        SubAgent(
            name="attribution-analyst",
            description="用 history_search 把当日资讯与指数波动做归因，每条归因挂上对应 news_id。",
            system_prompt=(
                "你是归因分析师。用 history_search 检索当日及近期资讯，"
                "把当日指数波动拆解成若干条因素，每条给出：因素名、方向（positive/negative）、"
                "权重（0-1，各条之和约为 1）、对应的 news_id 列表。"
                "禁止用行情描述充当原因（「指数涨了」是结果不是原因）；"
                "找不到消息面驱动时明确说明更多是资金与情绪因素，不要硬编归因。"
                "输出结构化的归因清单，供主分析师汇总。"
            ),
            tools=[history_search_tool],
        ),
    ]
    if settings.web_search_enabled:
        subs.append(
            SubAgent(
                name="outlook-analyst",
                description="用 web_search 检索机构解读、市场预期与海外反应，形成次日关注点。",
                system_prompt=(
                    "你是次日展望分析师。用 web_search 检索今日行情的机构解读、"
                    "市场预期、海外与外盘反应，以及明日可能影响开盘的事件。"
                    "只保留可信来源并标注信息时点，输出 3-6 条次日关注点，供主分析师汇总。"
                ),
                tools=[web_search_tool],
            ),
        )
    return subs


def _subagents_for(agent_type: AgentType, settings: Settings) -> list[SubAgent] | None:
    """按 Agent 类型返回并行子 agent（主 agent 只做汇总）。"""
    if agent_type == AgentType.MACRO_POLICY:
        return _macro_subagents(settings)
    if agent_type == AgentType.PRE_MARKET:
        return _pre_market_subagents(settings)
    if agent_type == AgentType.POST_MARKET:
        return _post_market_subagents(settings)
    return None


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
    subagents = _subagents_for(agent_type, settings)

    rf = response_format if response_format is not None else _response_format_for(settings)
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        subagents=subagents,
        response_format=rf,
        name=f"fin-news-{agent_type.value}",
    )


# 主 agent 工具集：声明式映射（新增 Agent 只改这张表，不再加特判分支）
MAIN_TOOLS: dict[AgentType, tuple[Any, ...]] = {
    # 宏观：需要外部信息（市场预期 / 海外反应）
    AgentType.MACRO_POLICY: (
        history_search_tool,
        web_search_tool,
        stock_lookup_tool,
        market_snapshot_tool,
    ),
    AgentType.INDUSTRY: (history_search_tool, stock_lookup_tool, market_snapshot_tool),
    AgentType.STOCK: (history_search_tool, stock_lookup_tool, market_snapshot_tool),
    # 盘前：隔夜外盘（us_daily 无权限，靠外部搜索补齐）
    AgentType.PRE_MARKET: (
        history_search_tool,
        web_search_tool,
        stock_lookup_tool,
        market_snapshot_tool,
    ),
    # 盘后：机构解读与次日预期
    AgentType.POST_MARKET: (
        history_search_tool,
        web_search_tool,
        stock_lookup_tool,
        market_snapshot_tool,
    ),
}

_DEFAULT_MAIN_TOOLS: tuple[Any, ...] = (
    history_search_tool,
    stock_lookup_tool,
    market_snapshot_tool,
)


def _main_tools(agent_type: AgentType, settings: Settings) -> list[Any]:
    """主 agent 的工具集（声明式映射 + 按可用性过滤）。

    历史检索 / 个股估值 / 市场快照都交给 Agent 按需调用（不再预取塞进 prompt）。
    外部搜索仅对声明了它的 Agent 开放，且未配置搜索服务时自动剔除——避免
    ReAct 循环里白调一个必定返回「不可用」的工具，白白消耗步数预算。
    """
    tools = list(MAIN_TOOLS.get(agent_type, _DEFAULT_MAIN_TOOLS))
    if not settings.web_search_enabled:
        tools = [t for t in tools if t is not web_search_tool]
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
    timeout_seconds: int | None = None,
    recursion_limit: int | None = None,
) -> AnalysisRun:
    """执行一次分析图，返回结构化结果（payload 为 AnalysisPayload 或 None）。

    timeout_seconds / recursion_limit 可由调用方覆盖：
    - 逐条资讯分析用 `analysis_timeout_seconds`（默认 300）
    - 盘前/盘后简报走深度多轮 ReAct，用 `brief_timeout_seconds`（默认 600）

    recursion_limit 必须通过 config 显式传入：LangGraph 默认 10007 等于没有上限，
    深度场景（多轮工具 + 子 agent）需要真正的步数刹车，否则只能靠超时兜底。
    """
    settings = settings or get_settings()
    timeout = timeout_seconds or settings.analysis_timeout_seconds
    limit = recursion_limit or settings.agent_recursion_limit
    started = time.perf_counter()
    compiled = graph if graph is not None else get_analysis_graph(agent_type, settings)

    try:
        result = await asyncio.wait_for(
            compiled.ainvoke(
                {"messages": [HumanMessage(content=user_prompt)]},
                config={"recursion_limit": limit},
            ),
            timeout=timeout,
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
