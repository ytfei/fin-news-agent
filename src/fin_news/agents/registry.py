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

from fin_news.core.config import LLMRole
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
    # 后续阶段补齐：
    # AgentType.MACRO_POLICY / INDUSTRY → deepagents
    # AgentType.STOCK / PRE_MARKET / POST_MARKET / QA → langgraph
}

_BUILDERS: dict[str, Builder] = {}


def register_builder(framework: str, builder: Builder) -> None:
    _BUILDERS[framework] = builder


_graphs: dict[tuple[AgentType, str], Any] = {}


def get_agent(agent_type: AgentType, settings: Any | None = None) -> Any:
    """按需构建并缓存 Agent 图（键 = agent_type + prompt_version）。"""
    spec = AGENT_SPECS.get(agent_type)
    if spec is None or not spec.enabled:
        raise KeyError(f"未注册或未启用的 Agent：{agent_type}")

    key = (agent_type, spec.prompt_version)
    if key not in _graphs:
        builder = _BUILDERS.get(spec.framework)
        if builder is None:
            raise KeyError(f"框架 {spec.framework} 没有注册构建器（agent={agent_type}）")
        _graphs[key] = builder(settings)
        logger.info("构建 Agent 图", agent=agent_type.value, version=spec.prompt_version,
                    framework=spec.framework)
    return _graphs[key]


def clear_cache() -> None:
    """清空图缓存（配置变更 / 测试用）。"""
    _graphs.clear()
