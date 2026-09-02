"""Agent 注册表：声明式描述每个 Agent 的构建方式，按需构建 + 图缓存。

新增一个 Agent 只需三步：
1. 在 `graphs/` 或 `analysis_agents.py` 里实现 build 函数
2. 在 `AGENT_SPECS` 里声明（框架 / 角色 / 工具 / 输出模型 / 版本）
3. 在 `_BUILDERS` 里注册框架对应的构建器

业务层只通过 `get_agent(agent_type)` 拿已编译的图，不感知框架细节。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fin_news.core.config import LLMRole, Settings, get_settings
from fin_news.core.enums import AgentType
from fin_news.core.logging import get_logger

logger = get_logger("agents.registry")

# 框架的构建器签名：build(settings) -> CompiledStateGraph
Builder = Callable[[Any], Any]


@dataclass(frozen=True)
class AgentSpec:
    agent_type: AgentType
    framework: str  # langgraph / deepagents
    model_role: LLMRole
    prompt_version: str
    tools: tuple[str, ...] = ()
    response_model: type | None = None
    checkpointer: bool = False
    recursion_limit: int = 12
    timeout_seconds: int | None = None
    enabled: bool = True
    note: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


AGENT_SPECS: dict[AgentType, AgentSpec] = {
    AgentType.SCORING: AgentSpec(
        agent_type=AgentType.SCORING,
        framework="langgraph",
        model_role="scoring",
        prompt_version="scoring.v1",
        response_model=None,  # 见 graphs/scoring_graph.py（ScoreBatchModel）
        recursion_limit=1 + 3 * 2,  # call/validate/rescue 最多循环 2 轮
        note="批量评分：高频低延迟，用显式图而不是自主 Agent",
    ),
    AgentType.MACRO_POLICY: AgentSpec(
        agent_type=AgentType.MACRO_POLICY,
        framework="deepagents",
        model_role="analysis",
        prompt_version="macro.v2",
        # 深度 ReAct：LangGraph 按节点计数且子 agent 内部也计步，12 会截断在检索中途
        recursion_limit=80,
        timeout_seconds=300,
        note="宏观/政策：DeepAgents + 子 agent（历史 / 传导 / 外部并行）",
    ),
    AgentType.INDUSTRY: AgentSpec(
        agent_type=AgentType.INDUSTRY,
        framework="deepagents",
        model_role="analysis",
        prompt_version="industry.v2",
        # 深度 ReAct：LangGraph 按节点计数且子 agent 内部也计步，12 会截断在检索中途
        recursion_limit=80,
        timeout_seconds=300,
        note="行业/产业：DeepAgents 自主检索 + 估值分析",
    ),
    AgentType.STOCK: AgentSpec(
        agent_type=AgentType.STOCK,
        framework="deepagents",
        model_role="analysis",
        prompt_version="stock.v2",
        # 深度 ReAct：LangGraph 按节点计数且子 agent 内部也计步，12 会截断在检索中途
        recursion_limit=80,
        timeout_seconds=300,
        note="个股事件：DeepAgents 精简版（估值 + 走势）",
    ),
    AgentType.PRE_MARKET: AgentSpec(
        agent_type=AgentType.PRE_MARKET,
        framework="deepagents",
        model_role="analysis",
        prompt_version="pre_market.v3",
        # 深度 ReAct：LangGraph 按节点计数且子 agent 内部也计步，12 会截断在检索中途
        recursion_limit=80,
        # 简报走多轮工具 + 外部检索 + 子 agent 并行，预算比逐条资讯分析更宽松
        timeout_seconds=600,
        note="盘前展望：上下文由 market_agents 预取内联，输出收敛到 AnalysisPayload（us_market / focus_directions 走 extras）",
    ),
    AgentType.POST_MARKET: AgentSpec(
        agent_type=AgentType.POST_MARKET,
        framework="deepagents",
        model_role="analysis",
        prompt_version="post_market.v3",
        # 深度 ReAct：LangGraph 按节点计数且子 agent 内部也计步，12 会截断在检索中途
        recursion_limit=80,
        # 简报走多轮工具 + 外部检索 + 子 agent 并行，预算比逐条资讯分析更宽松
        timeout_seconds=600,
        note="盘后复盘：同上，attribution 归因与 verdict 定调走 extras",
    ),
    AgentType.QA: AgentSpec(
        agent_type=AgentType.QA,
        # 追问依赖 SSE 流式 + 多轮 RAG，尚未图化；设计文档 P4 终态为 LangGraph RAG
        framework="legacy",
        model_role="qa",
        prompt_version="qa.v1",
        note="追问：需 SSE 流式，暂未图化，请直接用 qa_agent.QAAgent",
    ),
}

_BUILDERS: dict[str, Builder] = {}


def register_builder(framework: str, builder: Builder) -> None:
    _BUILDERS[framework] = builder


def _ensure_builders() -> None:
    """懒注册框架构建器（延迟导入，避免模块加载期就拉起 graphs / LLM 依赖）。

    langgraph 框架的评分图委托给 `scoring_graph.get_scoring_graph`，复用它已有的
    (version, provider, model) 缓存，避免两套缓存口径不一致。
    """
    if "langgraph" not in _BUILDERS:
        from fin_news.agents.graphs.scoring_graph import get_scoring_graph

        register_builder("langgraph", get_scoring_graph)


_graphs: dict[tuple[AgentType, str, str, str], Any] = {}


def _cache_key(
    agent_type: AgentType, spec: AgentSpec, settings: Settings | None
) -> tuple[AgentType, str, str, str]:
    """缓存键含 provider / model：切换模型时不会命中旧图。

    与 analysis_graphs.get_analysis_graph 的键口径保持一致。
    """
    s = settings or get_settings()
    return (
        agent_type,
        spec.prompt_version,
        s.llm_default_provider,
        s.model_for(s.llm_default_provider, spec.model_role),
    )


def get_agent(agent_type: AgentType, settings: Any | None = None) -> Any:
    """按需构建并缓存 Agent 图（键 = agent_type + version + provider + model）。

    deepagents 框架委托给 analysis_graphs.get_analysis_graph，避免两套缓存。
    """
    spec = AGENT_SPECS.get(agent_type)
    if spec is None or not spec.enabled:
        raise KeyError(f"未注册或未启用的 Agent：{agent_type}")

    # 兜底：builder 要用 settings 读 provider / model，不能为 None
    settings = settings or get_settings()

    if spec.framework == "legacy":
        raise NotImplementedError(
            f"{agent_type.value} 尚未图化：追问依赖 SSE 流式与多轮 RAG" +
            "（见 docs/05-agent-refactor-design.md P4），请直接使用 qa_agent.QAAgent"
        )

    if spec.framework == "deepagents":
        from fin_news.agents.graphs.analysis_graphs import get_analysis_graph

        return get_analysis_graph(agent_type, settings)

    _ensure_builders()
    key = _cache_key(agent_type, spec, settings)
    if key not in _graphs:
        builder = _BUILDERS.get(spec.framework)
        if builder is None:
            raise KeyError(f"框架 {spec.framework} 没有注册构建器（agent={agent_type}）")
        _graphs[key] = builder(settings)
        logger.info(
            "构建 Agent 图",
            agent=agent_type.value,
            version=spec.prompt_version,
            framework=spec.framework,
        )
    return _graphs[key]


def clear_cache() -> None:
    """清空图缓存（配置变更 / 测试用）。"""
    _graphs.clear()
    from fin_news.agents.graphs.analysis_graphs import clear_cache as _clear_analysis

    _clear_analysis()
