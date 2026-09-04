"""深度分析接口（GET /analysis/deep）的查询构造与响应模型测试。"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from fin_news.api.reporting import NEWS_ANALYSIS_AGENTS, VISIBLE_REPORT_STATUS, band_rank
from fin_news.api.routers import analysis as analysis_router
from fin_news.api.routers.analysis import (
    DEEP_ANALYSIS_MIN_SCORE,
    _bullets_of,
    _deep_filters,
    _deep_order_keys,
)
from fin_news.api.schemas import DeepAnalysisOut
from fin_news.core.enums import AgentType, ReportStatus, ScoreBand
from fin_news.domain.scoring import BAND_PRIORITY

PG = postgresql.dialect()
AnalysisReport = analysis_router.AnalysisReport
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def order_sql(sort: str, order: str = "desc") -> str:
    stmt = select(AnalysisReport.id).order_by(*_deep_order_keys(sort, order))
    return str(stmt.compile(dialect=PG))


def sql_of(conds) -> str:
    return str(select(AnalysisReport.id).where(*conds).compile(dialect=PG))


# ------------------------------------------------------------------ 固定口径


def test_deep_filters_restrict_to_news_agents_and_visible_status():
    conds = _deep_filters(agent_type=None, band=None, start=None, end=None, min_score=4)
    sql = sql_of(conds)
    assert "analysis_report.agent_type IN" in sql
    assert "analysis_report.news_id IS NOT NULL" in sql
    assert "analysis_report.status IN" in sql
    assert "analysis_report.score >=" in sql


def test_brief_agents_cannot_widen_deep_scope():
    """传入 pre_market 时只能在资讯级 Agent 内收窄，不能把简报放进来。"""
    conds = _deep_filters(
        agent_type=[AgentType.PRE_MARKET], band=None, start=None, end=None, min_score=None
    )
    assert len(conds) == 3  # agent_type / news_id / status
    assert AgentType.PRE_MARKET not in NEWS_ANALYSIS_AGENTS


def test_optional_filters_are_additive():
    base = len(_deep_filters(agent_type=None, band=None, start=None, end=None, min_score=None))
    with_band = _deep_filters(
        agent_type=None, band=[ScoreBand.MACRO], start=None, end=None, min_score=None
    )
    with_range = _deep_filters(agent_type=None, band=None, start=NOW, end=NOW, min_score=None)
    assert len(with_band) == base + 1
    assert len(with_range) == base + 2


def test_default_min_score_excludes_noise_band():
    """(0,3] 为噪声档，不会进入深度分析；默认门槛 4 即「评分 > 3」。"""
    assert DEEP_ANALYSIS_MIN_SCORE == 4
    assert ReportStatus.DRAFT not in VISIBLE_REPORT_STATUS
    assert ReportStatus.SUPERSEDED not in VISIBLE_REPORT_STATUS


# ------------------------------------------------------------------ 排序


@pytest.mark.parametrize("sort", ["published_at", "score", "impact"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_every_sort_ends_with_id_tiebreaker(sort, order):
    tail = (
        "analysis_report.id ASC NULLS FIRST"
        if order == "asc"
        else "analysis_report.id DESC NULLS LAST"
    )
    assert order_sql(sort, order).rstrip().endswith(tail)


def test_impact_sort_ranks_by_band_before_score():
    sql = order_sql("impact")
    assert "CASE" in sql
    assert sql.index("CASE") < sql.index("analysis_report.score")


def test_band_rank_covers_every_band_in_domain_priority():
    """分档权重必须完整覆盖 BAND_PRIORITY，漏掉一档就会静默排到 else 分支。"""
    sql = str(select(band_rank(AnalysisReport.band)).compile(dialect=PG))
    assert sql.count("WHEN") == len(BAND_PRIORITY)
    assert "ELSE" in sql


# ------------------------------------------------------------------ 响应模型


def test_bullets_preview_tolerates_malformed_content():
    """content 是 LLM 产出的 JSONB，结构不可信，必须逐层容错而不是抛异常。"""
    assert _bullets_of({}) == []
    assert _bullets_of({"bullets": "not-a-list"}) == []
    assert _bullets_of({"bullets": [1, 2, 3, 4]}) == ["1", "2", "3"]
    assert _bullets_of({"bullets": ["a", "b"]}) == ["a", "b"]


def test_deep_analysis_out_defaults():
    out = DeepAnalysisOut(id="r-1", agent_type="industry", title="标题", summary="摘要")
    assert out.bullets == []
    assert out.beneficiaries == []
    assert out.victims == []
    assert out.news_id is None
    assert out.disclaimer
