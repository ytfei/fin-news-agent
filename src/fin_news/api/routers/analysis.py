"""分析报告接口：深度分析列表 / 报告列表 / 详情。"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import ColumnElement, func, select

from fin_news.api.deps import PaginationDep, SessionDep
from fin_news.api.errors import NotFoundError
from fin_news.api.reporting import (
    NEWS_ANALYSIS_AGENTS,
    VISIBLE_REPORT_STATUS,
    band_rank,
    order_by_rank,
)
from fin_news.api.schemas import (
    AnalysisDetailOut,
    AnalysisReportOut,
    DeepAnalysisOut,
    Page,
)
from fin_news.core.enums import AgentType, ReportStatus, ScoreBand
from fin_news.models.analysis import AnalysisReport
from fin_news.models.news import NewsItem

router = APIRouter(prefix="/analysis", tags=["analysis"])

# 深度分析的评分下限：band 规则中 (0,3] 为噪声，只有评分 > 3 才会进入深度分析链路
DEEP_ANALYSIS_MIN_SCORE = 4

# 列表页预览的核心要点条数
BULLETS_PREVIEW = 3


def _deep_filters(
    *,
    agent_type: list[AgentType] | None,
    band: list[ScoreBand] | None,
    start: datetime | None,
    end: datetime | None,
    min_score: int | None,
) -> list[ColumnElement]:
    """深度分析列表的固定口径 + 可选过滤。

    固定口径：报告属于资讯级分析 Agent（排除盘前盘后简报）、绑定了具体资讯、
    状态对外可见、评分 > 3。
    """
    # 调用方传入的 agent_type 只用来在资讯级 Agent 范围内收窄，不能放宽到简报
    if agent_type:
        agents = [a for a in agent_type if a in NEWS_ANALYSIS_AGENTS]
    else:
        agents = NEWS_ANALYSIS_AGENTS
    conds: list[ColumnElement] = [
        AnalysisReport.agent_type.in_(agents),
        AnalysisReport.news_id.is_not(None),
        AnalysisReport.status.in_(VISIBLE_REPORT_STATUS),
    ]
    if min_score is not None:
        conds.append(AnalysisReport.score >= min_score)
    if band:
        conds.append(AnalysisReport.band.in_(band))
    if start:
        conds.append(AnalysisReport.published_at >= start)
    if end:
        conds.append(AnalysisReport.published_at <= end)
    return conds


def _deep_order_keys(sort: str, order: str) -> list[ColumnElement]:
    if sort == "impact":
        keys: list[ColumnElement] = [
            band_rank(AnalysisReport.band),  # 重要程度：宏观 > 行业 > 个股 > 噪声
            AnalysisReport.score,
            AnalysisReport.published_at,
        ]
    elif sort == "score":
        keys = [AnalysisReport.score, AnalysisReport.published_at]
    else:
        keys = [AnalysisReport.published_at]
    keys.append(AnalysisReport.id)
    return order_by_rank(keys, order)


# 注意：静态路径 /deep 必须声明在 /{report_id} 之前，否则会被路径参数吞掉
@router.get("/deep", response_model=Page[DeepAnalysisOut], summary="深度分析列表")
async def list_deep_analysis(
    session: SessionDep,
    pagination: PaginationDep,
    agent_type: list[AgentType] | None = Query(default=None),
    band: list[ScoreBand] | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    min_score: int | None = Query(default=DEEP_ANALYSIS_MIN_SCORE, ge=1, le=10),
    sort: str = Query(default="published_at", pattern="^(published_at|score|impact)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
):
    """评分 > 3 且 AI 已完成详细分析的资讯报告（宏观 / 行业 / 个股）。

    一次 JOIN 取回报告与原资讯，避免逐条回查 news_item 造成的 N+1。
    """
    conds = _deep_filters(
        agent_type=agent_type, band=band, start=start, end=end, min_score=min_score
    )

    join_on = NewsItem.id == AnalysisReport.news_id
    stmt = select(AnalysisReport, NewsItem).join(NewsItem, join_on)
    count_stmt = select(func.count()).select_from(AnalysisReport).join(NewsItem, join_on)
    for cond in conds:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    rows = (
        await session.execute(
            stmt.order_by(*_deep_order_keys(sort, order))
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()

    return Page[DeepAnalysisOut](
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
        has_more=(pagination.offset + len(rows)) < total,
        items=[_deep_out(report, news) for report, news in rows],
    )


def _deep_out(report: AnalysisReport, news: NewsItem | None) -> DeepAnalysisOut:
    return DeepAnalysisOut(
        id=str(report.public_id),
        agent_type=report.agent_type.value,
        news_id=str(news.public_id) if news is not None else None,
        news_title=news.title if news is not None else None,
        news_source=news.src_name or news.src or news.source if news is not None else None,
        news_publish_time=news.publish_time if news is not None else None,
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
        bullets=_bullets_of(report.content or {}),
        published_at=report.published_at,
    )


def _bullets_of(content: dict[str, Any]) -> list[str]:
    """核心要点预览：content 是 LLM 产出的 JSONB，结构不可信，逐层容错。"""
    bullets = content.get("bullets")
    if not isinstance(bullets, list):
        return []
    return [str(b) for b in bullets[:BULLETS_PREVIEW]]


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
            stmt.order_by(*order_by_rank([AnalysisReport.published_at, AnalysisReport.id], "desc"))
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).scalars().all()

    # 批量回查原资讯标题，避免每条报告各查一次（N+1）
    news_meta = await _news_meta(session, [r.news_id for r in rows if r.news_id])

    return Page[AnalysisReportOut](
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
        has_more=(pagination.offset + len(rows)) < total,
        items=[_to_out(r, news_meta) for r in rows],
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

    news_meta = await _news_meta(session, [report.news_id] if report.news_id else [])
    out = _to_out(report, news_meta)
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


async def _news_meta(session, news_ids: list[int]) -> dict[int, tuple[str, str]]:
    """批量取回 (news_id) -> (title, public_id)。"""
    if not news_ids:
        return {}
    rows = await session.execute(
        select(NewsItem.id, NewsItem.title, NewsItem.public_id).where(NewsItem.id.in_(news_ids))
    )
    return {r[0]: (r[1], str(r[2])) for r in rows.all()}


def _to_out(report: AnalysisReport, news_meta: dict[int, tuple[str, str]]) -> AnalysisReportOut:
    news_title = None
    news_public_id = None
    if report.news_id is not None and report.news_id in news_meta:
        news_title, news_public_id = news_meta[report.news_id]

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
