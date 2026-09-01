"""分析 Agent 图（DeepAgents）的纯逻辑单测（不发起真实模型请求）。"""
from __future__ import annotations

from fin_news.agents.graphs.analysis_graphs import (
    AGENT_GRAPH_CONFIG,
    AnalysisRun,
    _main_tools,
    _macro_subagents,
    _model_of,
    _usage_of,
    build_analysis_graph,
    run_analysis,
)
from fin_news.agents.schemas import AnalysisPayload, EntityItemModel
from fin_news.core.config import Settings
from fin_news.core.enums import AgentType


def _settings(**kw) -> Settings:
    base = dict(
        volcengine_api_key="vk",
        deepseek_api_key="dk",
        llm_default_provider="volcengine",
        llm_fallback_provider="deepseek",
        web_search_enabled=False,
    )
    base.update(kw)
    return Settings(_env_file=None, **base)


# ------------------------------ 输出模型 ------------------------------


def test_analysis_payload_defaults():
    payload = AnalysisPayload()
    assert payload.headline == ""
    assert payload.summary == ""
    assert payload.sentiment == "neutral"
    assert payload.impact_level == "medium"
    assert payload.horizon == "short"
    assert payload.confidence == 0.6


def test_analysis_payload_model_dump_is_json_friendly():
    payload = AnalysisPayload(
        headline="央行降准利好券商",
        summary="短期提振风险偏好",
        beneficiaries=[EntityItemModel(code="BK0473", name="证券", type="sector", reason="流动性改善")],
    )
    data = payload.model_dump()
    assert data["headline"] == "央行降准利好券商"
    assert data["beneficiaries"][0]["code"] == "BK0473"
    # 列表字段必须是可 JSON 序列化的普通 list/dict
    assert isinstance(data["beneficiaries"], list)
    assert isinstance(data["beneficiaries"][0], dict)


# ------------------------------ 图配置 ------------------------------


def test_agent_graph_config_covers_analysis_types():
    assert set(AGENT_GRAPH_CONFIG) == {
        AgentType.MACRO_POLICY,
        AgentType.INDUSTRY,
        AgentType.STOCK,
    }


def test_macro_subagents_includes_external_only_when_enabled():
    off = _macro_subagents(_settings(web_search_enabled=False))
    on = _macro_subagents(_settings(web_search_enabled=True))
    names_off = [s["name"] for s in off]
    names_on = [s["name"] for s in on]
    assert "history-analyst" in names_off
    assert "transmission-analyst" in names_off
    assert "external-analyst" not in names_off
    assert "external-analyst" in names_on


def test_main_tools_include_web_search_only_for_macro():
    macro = _main_tools(AgentType.MACRO_POLICY, _settings(web_search_enabled=True))
    industry = _main_tools(AgentType.INDUSTRY, _settings(web_search_enabled=True))
    macro_names = {getattr(t, "name", None) for t in macro}
    industry_names = {getattr(t, "name", None) for t in industry}
    assert "web_search" in macro_names
    assert "web_search" not in industry_names


# ------------------------------ 图构建 ------------------------------


def test_build_analysis_graph_macro_has_subagents():
    graph = build_analysis_graph(AgentType.MACRO_POLICY, _settings())
    assert graph is not None


def test_build_analysis_graph_industry_and_stock():
    assert build_analysis_graph(AgentType.INDUSTRY, _settings()) is not None
    assert build_analysis_graph(AgentType.STOCK, _settings()) is not None


# ------------------------------ 结果提取 ------------------------------


class _FakeMsg:
    def __init__(self, input_tokens=0, output_tokens=0, model=""):
        self.usage_metadata = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        self.response_metadata = {"model_name": model}


def test_usage_of_sums_all_messages():
    result = {"messages": [_FakeMsg(10, 5, "m1"), _FakeMsg(20, 7, "m2")]}
    assert _usage_of(result) == (30, 12)


def test_model_of_takes_last_message_model():
    result = {"messages": [_FakeMsg(1, 1, "a"), _FakeMsg(1, 1, "b")]}
    assert _model_of(result) == "b"


def test_model_of_empty_messages():
    assert _model_of({"messages": []}) == ""


# ------------------------------ run_analysis ------------------------------


class _FakeGraph:
    """可注入的假图：ainvoke 返回预设结果。"""

    def __init__(self, result):
        self._result = result

    async def ainvoke(self, state):
        return self._result


async def test_run_analysis_extracts_structured_response():
    payload = AnalysisPayload(headline="测试", summary="摘要")
    graph = _FakeGraph({"messages": [], "structured_response": payload})
    run = await run_analysis(AgentType.STOCK, "用户提示", _settings(), graph=graph)
    assert isinstance(run, AnalysisRun)
    assert run.payload is payload
    assert run.error is None


async def test_run_analysis_normalizes_dict_payload():
    graph = _FakeGraph({"messages": [], "structured_response": {"headline": "H", "summary": "S"}})
    run = await run_analysis(AgentType.STOCK, "提示", _settings(), graph=graph)
    assert isinstance(run.payload, AnalysisPayload)
    assert run.payload.headline == "H"


async def test_run_analysis_none_payload_is_not_error():
    graph = _FakeGraph({"messages": [], "structured_response": None})
    run = await run_analysis(AgentType.STOCK, "提示", _settings(), graph=graph)
    assert run.payload is None
