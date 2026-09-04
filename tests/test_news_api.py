"""资讯接口的查询构造与路由注册测试。

仓库内没有数据库 fixture（现有测试都是纯单元测试），因此这里不连库跑 SQL，
而是断言 ORM 表达式编译出的 SQL 结构 —— 排序规则与过滤口径的正确性正是本次
改动的核心，且不需要真实数据即可验证。
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from fin_news.api.routers import news as news_router
from fin_news.api.routers.news import _build_filters, _order_keys
from fin_news.core.enums import ScoreBand

PG = postgresql.dialect()
NewsItem = news_router.NewsItem
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def compile_sql(conds) -> str:
    return str(select(NewsItem.id).where(*conds).compile(dialect=PG))


def order_sql(sort: str, order: str = "desc") -> str:
    return str(select(NewsItem.id).order_by(*_order_keys(sort, order)).compile(dialect=PG))


# ------------------------------------------------------------------ 排序


def test_impact_sort_uses_band_priority_not_raw_score():
    """impact 必须按分档优先级排序；退化成按 score 排序是本接口的历史缺陷。"""
    sql = order_sql("impact")
    assert "CASE" in sql
    assert sql.index("CASE") < sql.index("news_item.score DESC")


@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        ("publish_time", "desc", "news_item.publish_time DESC NULLS LAST"),
        ("publish_time", "asc", "news_item.publish_time ASC NULLS FIRST"),
        ("score", "desc", "news_item.score DESC NULLS LAST"),
        ("score", "asc", "news_item.score ASC NULLS FIRST"),
    ],
)
def test_sort_direction_and_null_handling(sort, order, expected):
    assert expected in order_sql(sort, order)


@pytest.mark.parametrize("sort", ["publish_time", "score", "impact"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_every_sort_ends_with_id_tiebreaker(sort, order):
    """末位主键兜底：顺序必须完全确定，否则 offset 分页会重复 / 漏数据。"""
    tail = "news_item.id ASC NULLS FIRST" if order == "asc" else "news_item.id DESC NULLS LAST"
    assert order_sql(sort, order).rstrip().endswith(tail)


def test_score_sort_has_publish_time_secondary_key():
    sql = order_sql("score")
    assert sql.index("news_item.score") < sql.index("news_item.publish_time")


# ------------------------------------------------------------------ 过滤


def test_has_analysis_true_pushes_down_to_exists_subquery():
    sql = compile_sql(_build_filters(has_analysis=True))
    assert "EXISTS" in sql
    assert "analysis_report" in sql


def test_has_analysis_false_is_no_longer_a_noop():
    """旧实现里 has_analysis=False 完全不过滤，这里必须生效（取反 EXISTS）。"""
    sql = compile_sql(_build_filters(has_analysis=False))
    assert "EXISTS" in sql
    assert "NOT" in sql


def test_time_filter_start_and_end_are_independent():
    """只给 end 时不应再附加「近 24 小时」下界（旧实现的语义 bug）。"""
    only_start = compile_sql(_build_filters(start=NOW))
    assert "news_item.publish_time >=" in only_start
    assert "news_item.publish_time <=" not in only_start

    only_end = compile_sql(_build_filters(end=NOW))
    assert "news_item.publish_time <=" in only_end
    assert "news_item.publish_time >=" not in only_end

    both = compile_sql(_build_filters(start=NOW, end=NOW))
    assert "news_item.publish_time >=" in both
    assert "news_item.publish_time <=" in both


def test_default_since_is_not_stacked_on_top_of_start():
    """start 已给定时不该再叠加 default_since，否则时间下界会被错误抬高。"""
    assert len(_build_filters(start=NOW, default_since=NOW)) == 1
    assert len(_build_filters(default_since=NOW)) == 1


def test_other_filters_are_applied():
    assert "news_item.score >=" in compile_sql(_build_filters(min_score=6))
    assert "news_item.score <=" in compile_sql(_build_filters(max_score=8))
    assert "IN" in compile_sql(_build_filters(band=[ScoreBand.MACRO]))
    assert "IN" in compile_sql(_build_filters(source=["cls"]))
    assert "ILIKE" in compile_sql(_build_filters(q="降准")).upper()
    assert "news_entity" in compile_sql(_build_filters(code="600519.SH"))


def test_no_filters_yields_no_conditions():
    assert _build_filters() == []
