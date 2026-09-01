"""评分分档与 Agent 路由（纯函数，便于单测）。"""
from __future__ import annotations

from fin_news.core.enums import AgentType, ScoreBand

MIN_SCORE, MAX_SCORE = 1, 10

# 左开右闭：(0,3]=NOISE (3,5]=STOCK (5,7]=INDUSTRY (7,10]=MACRO
BAND_RULES: list[tuple[int, int, ScoreBand]] = [
    (0, 3, ScoreBand.NOISE),
    (3, 5, ScoreBand.STOCK),
    (5, 7, ScoreBand.INDUSTRY),
    (7, 10, ScoreBand.MACRO),
]

BAND_PRIORITY: dict[ScoreBand, int] = {
    ScoreBand.MACRO: 3,
    ScoreBand.INDUSTRY: 2,
    ScoreBand.STOCK: 1,
    ScoreBand.NOISE: 0,
}

BAND_AGENT: dict[ScoreBand, AgentType] = {
    ScoreBand.MACRO: AgentType.MACRO_POLICY,
    ScoreBand.INDUSTRY: AgentType.INDUSTRY,
    ScoreBand.STOCK: AgentType.STOCK,
}


def clamp_score(score: int | float | None) -> int:
    if score is None:
        raise ValueError("score 不能为空")
    return max(MIN_SCORE, min(MAX_SCORE, int(round(float(score)))))


def band_for_score(score: int | float | None) -> ScoreBand:
    value = clamp_score(score)
    for low, high, band in BAND_RULES:
        if low < value <= high:
            return band
    return ScoreBand.NOISE


def agent_for_score(score: int | float | None) -> AgentType | None:
    """评分 -> 深度分析 Agent；(0,3] 噪声不分析。"""
    return BAND_AGENT.get(band_for_score(score))


def priority_for_score(score: int | float | None) -> int:
    return BAND_PRIORITY[band_for_score(score)] or 1


def should_vectorize(score: int | None, threshold: int = 3) -> bool:
    """score > threshold 才做向量化与深度分析。"""
    return score is not None and score > threshold


def should_analyze(score: int | None, threshold: int = 3) -> bool:
    return should_vectorize(score, threshold)
