"""分析报告接口。"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import desc, func, select

from fin_news.api.deps import PaginationDep, SessionDep
from fin_news.api.errors import NotFoundError
from fin_news.api.schemas import AnalysisDetailOut, AnalysisReportOut, Page
from fin_news.core.enums import AgentType, ReportStatus, ScoreBand
from fin_news.models.analysis import AnalysisReport
from fin_news.models.news import NewsItem

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("", response_model=Page[AnalysisReportOut], summary="分析报告列表")
async def list_analysis(
    session: SessionDep,
    pagination: PaginationDep,
    agent_type: AgentType | None = Query(default=None),
    band: ScoreBand | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    min_score: int | None = Query(default=None, ge=1, le=10),
    code: str | None = Query(default=None),
    status: str = Query(default="PUBLISHED", pattern="^(PUBLISHED|DEGRADED)$"),
):
    stmt = select(AnalysisReport)
    count_stmt = select(func.count()).select_from(AnalysisReport)

    stmt = stmt.where(AnalysisReport.status == ReportStatus(status))
    count_stmt = count_stmt.where(AnalysisReport.status == ReportStatus(status))

    if agent_type:
        stmt = stmt.where(AnalysisReport.agent_type == agent_type)
        count_stmt = count_stmt.where(AnalysisReport.agent_type == agent_type)
    if band:
        stmt = stmt.where(AnalysisReport.band == band)
        count_stmt = count_stmt.where(AnalysisReport.band == band)
    if start:
        stmt = stmt.where(AnalysisReport.published_at >= start)
        count_stmt = count_stmt.where(AnalysisReport.published_at >= start)
    if end:
        stmt = stmt.where(AnalysisReport.published_at <= end)
        count_stmt = count_stmt.where(AnalysisReport.published_at <= end)
    if min_score is not None:
        stmt = stmt.where(AnalysisReport.score >= min_score)
        count_stmt = count_stmt.where(AnalysisReport.score >= min_score)
    if code:
        stmt = stmt.where(AnalysisReport.entities.contains([{"code": code}]))
        count_stmt = count_stmt.where(AnalysisReport.entities.contains([{"code": code}]))

    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    rows = (
        await session.execute(
            stmt.order_by(desc(AnalysisReport.published_at))
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).scalars().all()

    return Page[AnalysisReportOut](
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
        has_more=(pagination.offset + len(rows)) < total,
        items=[await _to_out(session, r) for r in rows],
    )


@router.get("/{report_id}", response_model=AnalysisDetailOut, summary="分析报告详情")
async def get_analysis(report_id: str, session: SessionDep):
    try:
        uid = UUID(report_id)
    except ValueError as exc:
        raise NotFoundError("报告不存在") from exc

    report = (
        await session.execute(select(AnalysisReport).where(AnalysisReport.public_id == uid))
    ).scalar_one_or_none()
    if report is None:
        raise NotFoundError("报告不存在")

    out = await _to_out(session, report)
    return AnalysisDetailOut(
        **out.model_dump(),
        content=report.content or {},
        external_sources=report.external_sources or [],
        run={
            "run_id": report.run_id,
            "latency_ms": report.latency_ms,
            "prompt_tokens": None,
            "completion_tokens": None,
            "attempts": None,
        },
    )


async def _to_out(session, report: AnalysisReport) -> AnalysisReportOut:
    news_title = None
    news_public_id = None
    if report.news_id:
        news = (
            await session.execute(
                select(NewsItem.title, NewsItem.public_id).where(NewsItem.id == report.news_id)
            )
        ).first()
        if news:
            news_title, news_public_id = news[0], str(news[1])

    return AnalysisReportOut(
        id=str(report.public_id),
        agent_type=report.agent_type.value,
        news_id=news_public_id,
        news_title=news_title,
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
    )
