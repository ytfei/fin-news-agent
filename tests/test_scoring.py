"""评分分档与 Agent 路由（需求文档 §4 F2 的分档规则）。"""
import pytest

from fin_news.core.enums import AgentType, ScoreBand
from fin_news.domain.scoring import (
    agent_for_score,
    band_for_score,
    clamp_score,
    should_vectorize,
)


@pytest.mark.parametrize(
    "score,band",
    [
        (1, ScoreBand.NOISE),
        (3, ScoreBand.NOISE),          # (0,3] 噪声
        (4, ScoreBand.STOCK),          # (3,5] 个股
        (5, ScoreBand.STOCK),
        (6, ScoreBand.INDUSTRY),       # (5,7] 行业
        (7, ScoreBand.INDUSTRY),
        (8, ScoreBand.MACRO),          # (7,10] 宏观
        (10, ScoreBand.MACRO),
    ],
)
def test_band_boundaries(score, band):
    assert band_for_score(score) is band


@pytest.mark.parametrize(
    "score,agent",
    [
        (9, AgentType.MACRO_POLICY),
        (8, AgentType.MACRO_POLICY),
        (7, AgentType.INDUSTRY),
        (6, AgentType.INDUSTRY),
        (5, AgentType.STOCK),
        (4, AgentType.STOCK),
        (3, None),                     # 噪声不分析
        (1, None),
    ],
)
def test_agent_routing(score, agent):
    assert agent_for_score(score) is agent


def test_score_threshold_vectorize():
    """score > 3 才向量化（默认阈值 3）。"""
    assert should_vectorize(4) is True
    assert should_vectorize(3) is False
    assert should_vectorize(None) is False
    assert should_vectorize(3, threshold=2) is True


@pytest.mark.parametrize("raw,expected", [(-5, 1), (0, 1), (7.4, 7), (7.6, 8), (99, 10)])
def test_clamp_score(raw, expected):
    assert clamp_score(raw) == expected


def test_clamp_score_rejects_none():
    with pytest.raises(ValueError):
        clamp_score(None)
