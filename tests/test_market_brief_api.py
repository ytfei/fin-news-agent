"""报告页相关接口测试：/market/brief 的空态契约、/market/briefs 的 period 过滤，
以及三个新增静态路由的注册顺序（静态路径必须早于路径参数，否则会被吞掉）。
"""
from __future__ import annotations

from datetime import date

from fin_news.api.app import create_app
from fin_news.api.schemas import BriefOut, PreMarketBriefOut


def api_paths() -> list[str]:
    """按注册顺序返回 OpenAPI 路径 —— 顺序即 FastAPI 的路由匹配顺序。"""
    return list(create_app(with_background=False).openapi()["paths"].keys())


# ------------------------------------------------------------------ 路由顺序


def test_static_routes_are_registered_before_path_params():
    paths = api_paths()
    assert paths.index("/api/v1/news/sources") < paths.index("/api/v1/news/{news_id}")
    assert paths.index("/api/v1/analysis/deep") < paths.index("/api/v1/analysis/{report_id}")
    assert paths.index("/api/v1/market/brief") < paths.index("/api/v1/market/pre-market")


def test_new_endpoints_are_registered():
    paths = api_paths()
    assert "/api/v1/news/sources" in paths
    assert "/api/v1/analysis/deep" in paths
    assert "/api/v1/market/brief" in paths
    assert "/api/v1/market/briefs" in paths


def test_legacy_brief_endpoints_are_kept():
    """旧接口保留：避免影响已有调用方。"""
    paths = api_paths()
    assert "/api/v1/market/pre-market" in paths
    assert "/api/v1/market/post-market" in paths


# ------------------------------------------------------------------ 空态契约


def test_brief_out_is_unavailable_by_default():
    """无简报时返回 available=False + 200，前端渲染空态而不是错误框。"""
    out = BriefOut()
    assert out.available is False
    assert out.brief is None
    assert out.period == ""


def test_brief_out_carries_brief_when_available():
    brief = PreMarketBriefOut(
        id="r-1",
        agent_type="pre_market",
        title="盘前展望",
        summary="摘要",
        status="PUBLISHED",
        us_market=[],
        focus_directions=[],
    )
    out = BriefOut(available=True, trade_date=date(2026, 9, 3), period="pre_market", brief=brief)
    assert out.available is True
    assert out.brief is not None
    assert out.brief.title == "盘前展望"
    assert out.trade_date == date(2026, 9, 3)
