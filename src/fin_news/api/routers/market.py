"""市场接口：概览 / 盘前 / 盘后 / 简报列表 / 交易日历。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import desc, select

from fin_news.agents.tools.market_data import latest_trade_date, market_snapshot, us_overnight
from fin_news.api.deps import SessionDep
from fin_news.api.errors import NotFoundError
from fin_news.api.reporting import VISIBLE_REPORT_STATUS
from fin_news.api.schemas import (
    AnalysisDetailOut,
    BriefMetaOut,
    BriefOut,
    MarketOverviewOut,
    PostMarketBriefOut,
    PreMarketBriefOut,
)
from fin_news.core.enums import AgentType, MarketPeriod
from fin_news.models.analysis import AnalysisReport, TradeCalendar

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/overview", response_model=MarketOverviewOut, summary="市场概览")
async def get_overview(session: SessionDep, date_: date | None = Query(default=None, alias="date")):
    trade_date = date_ or await latest_trade_date(session) or date.today()
    snapshot = await market_snapshot(session, trade_date)

    headline = None
    brief = await _get_brief(session, trade_date, MarketPeriod.POST_MARKET)
    if brief is not None:
        extras = (brief.content or {}).get("extras") or {}
        headline = (extras.get("verdict") or {}).get("one_liner") or brief.summary

    return MarketOverviewOut(
        trade_date=trade_date,
        is_trading_day=bool(snapshot.get("ready", True)),
        updated_at=datetime.now(),
        indices=snapshot.get("index_bars") or [],
        breadth=snapshot.get("breadth"),
        sectors_top=snapshot.get("sectors_top") or [],
        sectors_bottom=snapshot.get("sectors_bottom") or [],
        headline=headline,
    )


@router.get(
    "/brief",
    response_model=BriefOut,
    summary="盘前 / 盘后简报（无数据返回 available=false，不报错）",
)
async def get_brief(
    session: SessionDep,
    period: MarketPeriod = Query(default=MarketPeriod.PRE_MARKET),
    date_: date | None = Query(default=None, alias="date"),
):
    """报告页正文数据源。

    旧接口 /pre-market、/post-market 在无简报时抛 404，前端会显示错误框；报告页需要
    「切到没有简报的日期」是正常操作，因此这里用 available 标志 + 空态代替异常。
    """
    trade_date = date_ or await latest_trade_date(session) or date.today()
    report = await _get_brief(session, trade_date, period)
    if report is None:
        return BriefOut(
            available=False,
            trade_date=trade_date,
            period=period.value,
            brief=None,
            message=f"{trade_date} 暂无{'盘前' if period == MarketPeriod.PRE_MARKET else '盘后'}简报",
        )

    brief: PreMarketBriefOut | PostMarketBriefOut
    if period == MarketPeriod.PRE_MARKET:
        brief = await _pre_market_brief(session, report, trade_date)
    else:
        brief = _post_market_brief(report)
    return BriefOut(available=True, trade_date=trade_date, period=period.value, brief=brief)


@router.get("/pre-market", response_model=PreMarketBriefOut, summary="盘前展望")
async def get_pre_market(session: SessionDep, date_: date | None = Query(default=None, alias="date")):
    trade_date = date_ or await latest_trade_date(session) or date.today()
    report = await _get_brief(session, trade_date, MarketPeriod.PRE_MARKET)
    if report is None:
        raise NotFoundError("该交易日暂无盘前简报（可能尚未生成）")
    return await _pre_market_brief(session, report, trade_date)


@router.get("/post-market", response_model=PostMarketBriefOut, summary="盘后复盘")
async def get_post_market(session: SessionDep, date_: date | None = Query(default=None, alias="date")):
    trade_date = date_ or await latest_trade_date(session) or date.today()
    report = await _get_brief(session, trade_date, MarketPeriod.POST_MARKET)
    if report is None:
        raise NotFoundError("该交易日暂无盘后简报（可能尚未生成）")
    return _post_market_brief(report)


@router.get("/briefs", response_model=list[BriefMetaOut], summary="近期简报列表")
async def list_briefs(
    session: SessionDep,
    days: int = Query(default=30, ge=1, le=180),
    period: MarketPeriod | None = Query(default=None),
):
    """报告页的历史归档。指定 period 时只返回该时段的简报。"""
    since = date.today() - timedelta(days=days)
    conds = [
        AnalysisReport.agent_type.in_([AgentType.PRE_MARKET, AgentType.POST_MARKET]),
        AnalysisReport.trade_date >= since,
        AnalysisReport.status.in_(VISIBLE_REPORT_STATUS),
    ]
    if period is not None:
        conds.append(AnalysisReport.period == period)

    rows = (
        await session.execute(
            select(AnalysisReport)
            .where(*conds)
            .order_by(desc(AnalysisReport.trade_date), desc(AnalysisReport.published_at))
            .limit(days * 2)
        )
    ).scalars().all()
    return [
        BriefMetaOut(
            trade_date=r.trade_date,
            period=r.period.value if r.period else "",
            report_id=str(r.public_id),
            title=r.title,
            summary=r.summary,
            published_at=r.published_at,
        )
        for r in rows
        if r.trade_date and r.period
    ]


@router.get("/calendar", summary="交易日历")
async def get_calendar(
    session: SessionDep,
    start: date = Query(...),
    end: date = Query(...),
):
    rows = await session.execute(
        select(TradeCalendar)
        .where(TradeCalendar.exchange == "SSE", TradeCalendar.cal_date >= start, TradeCalendar.cal_date <= end)
        .order_by(TradeCalendar.cal_date)
    )
    return [{"date": r.cal_date, "is_open": r.is_open} for r in rows.scalars().all()]


# ----------------------------------------------------------------------
async def _get_brief(session, trade_date: date, period: MarketPeriod) -> AnalysisReport | None:
    return (
        await session.execute(
            select(AnalysisReport)
            .where(
                AnalysisReport.trade_date == trade_date,
                AnalysisReport.period == period,
                AnalysisReport.status.in_(VISIBLE_REPORT_STATUS),
            )
            .order_by(desc(AnalysisReport.published_at).nullslast())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _pre_market_brief(
    session, report: AnalysisReport, trade_date: date
) -> PreMarketBriefOut:
    extras = (report.content or {}).get("extras") or {}
    return PreMarketBriefOut(
        **_brief_base(report).model_dump(),
        # 报告里没带隔夜美股时（无行情权限 / 旧版本），现场查一次行情表兜底
        us_market=extras.get("us_market") or (await us_overnight(session, trade_date)),
        focus_directions=extras.get("focus_directions") or [],
    )


def _post_market_brief(report: AnalysisReport) -> PostMarketBriefOut:
    extras = (report.content or {}).get("extras") or {}
    return PostMarketBriefOut(
        **_brief_base(report).model_dump(),
        verdict=extras.get("verdict") or {},
        attribution=extras.get("attribution") or [],
        next_day_focus=extras.get("next_day_focus") or [],
    )


def _brief_base(report: AnalysisReport) -> AnalysisDetailOut:
    return AnalysisDetailOut(
        id=str(report.public_id),
        agent_type=report.agent_type.value,
        news_id=None,
        news_title=None,
        trade_date=report.trade_date,
        title=report.title,
        summary=report.summary,
        score=report.score,
        band=report.band.value if report.band else None,
        sentiment=report.sentiment,
        impact_level=report.impact_level,
        horizon=report.horizon,
        confidence=report.confidence,
        beneficiaries=report.beneficiaries or [],
        victims=report.victims or [],
        entities=report.entities or [],
        references=report.references or [],
        status=report.status.value,
        model=report.model,
        prompt_version=report.prompt_version,
        published_at=report.published_at,
        content=report.content or {},
        external_sources=report.external_sources or [],
        run={"run_id": report.run_id, "latency_ms": report.latency_ms},
    )
