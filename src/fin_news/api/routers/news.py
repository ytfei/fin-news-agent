"""资讯接口：列表 / 详情 / 关联分析 / 相似资讯。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from fin_news.api.deps import PaginationDep, SessionDep
from fin_news.api.errors import NotFoundError
from fin_news.api.schemas import (
    EntityOut,
    NewsDetailOut,
    NewsItemOut,
    Page,
    RelatedNewsOut,
    ScoreHistoryOut,
)
from fin_news.core.enums import AgentType, ReportStatus, ScoreBand
from fin_news.core.timeutil import now
from fin_news.models.analysis import AnalysisReport
from fin_news.models.news import NewsEntity, NewsItem, NewsScore

router = APIRouter(prefix="/news", tags=["news"])


@router.get("", response_model=Page[NewsItemOut], summary="资讯流")
async def list_news(
    session: SessionDep,
    pagination: PaginationDep,
    band: list[ScoreBand] | None = Query(default=None),
    min_score: int | None = Query(default=None, ge=1, le=10),
    max_score: int | None = Query(default=None, ge=1, le=10),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    source: list[str] | None = Query(default=None, description="来源标识 cls / wallstreetcn"),
    q: str | None = Query(default=None, description="标题关键词"),
    code: str | None = Query(default=None, description="关联标的代码"),
    has_analysis: bool | None = Query(default=None),
    sort: str = Query(default="publish_time", pattern="^(publish_time|score|impact)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
):
    stmt = select(NewsItem)
    count_stmt = select(func.count()).select_from(NewsItem)

    if start or end:
        stmt = stmt.where(NewsItem.publish_time >= (start or now() - timedelta(days=1)))
        count_stmt = count_stmt.where(NewsItem.publish_time >= (start or now() - timedelta(days=1)))
        if end:
            stmt = stmt.where(NewsItem.publish_time <= end)
            count_stmt = count_stmt.where(NewsItem.publish_time <= end)
    else:
        # 默认近 24 小时
        since = now() - timedelta(days=1)
        stmt = stmt.where(NewsItem.publish_time >= since)
        count_stmt = count_stmt.where(NewsItem.publish_time >= since)

    if band:
        stmt = stmt.where(NewsItem.band.in_(band))
        count_stmt = count_stmt.where(NewsItem.band.in_(band))
    if min_score is not None:
        stmt = stmt.where(NewsItem.score >= min_score)
        count_stmt = count_stmt.where(NewsItem.score >= min_score)
    if max_score is not None:
        stmt = stmt.where(NewsItem.score <= max_score)
        count_stmt = count_stmt.where(NewsItem.score <= max_score)
    if source:
        stmt = stmt.where(NewsItem.src.in_(source))
        count_stmt = count_stmt.where(NewsItem.src.in_(source))
    if q:
        pattern = f"%{q}%"
        cond = or_(NewsItem.title.ilike(pattern), NewsItem.content.ilike(pattern))
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if code:
        subq = select(NewsEntity.news_id).where(NewsEntity.code == code)
        stmt = stmt.where(NewsItem.id.in_(subq))
        count_stmt = count_stmt.where(NewsItem.id.in_(subq))

    if sort == "score":
        key = NewsItem.score
    elif sort == "impact":
        key = NewsItem.score
    else:
        key = NewsItem.publish_time
    stmt = stmt.order_by(key.desc() if order == "desc" else key.asc())

    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    stmt = stmt.offset(pagination.offset).limit(pagination.page_size)
    items = (await session.execute(stmt)).scalars().all()

    if has_analysis is not None:
        items = [i for i in items if i.analysis_status == "DONE"] if has_analysis else items

    news_ids = [i.id for i in items]
    analysis_map = await _latest_analysis(session, news_ids)
    entity_map = await _entities(session, news_ids)

    return Page[NewsItemOut](
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
        has_more=(pagination.offset + len(items)) < total,
        items=[_to_out(i, analysis_map.get(i.id), entity_map.get(i.id, [])) for i in items],
    )


@router.get("/{news_id}", response_model=NewsDetailOut, summary="资讯详情")
async def get_news(news_id: str, session: SessionDep):
    news = await _get_news_by_public_id(session, news_id)
    analysis = await _latest_analysis(session, [news.id])
    entities = await _entities(session, [news.id])

    scores = (
        await session.execute(
            select(NewsScore).where(NewsScore.news_id == news.id).order_by(NewsScore.created_at.desc())
        )
    ).scalars().all()

    related = []
    try:
        from fin_news.agents.tools.retrieval import related_news

        hits = await related_news(session, news.id, limit=6)
        id_map = await _public_ids(session, [h.news_id for h in hits])
        related = [
            RelatedNewsOut(
                id=id_map.get(h.news_id, ""),
                title=h.title,
                publish_time=h.publish_time,
                score=h.score,
                similarity=h.similarity,
            )
            for h in hits
            if h.news_id in id_map
        ]
    except Exception:  # noqa: BLE001 - 向量检索失败不影响详情
        related = []

    out = NewsDetailOut(
        **_to_out(news, analysis.get(news.id), entities.get(news.id, [])).model_dump(),
        content=news.content,
        content_truncated=news.content_truncated,
        url=news.url,
        score_history=[
            ScoreHistoryOut(
                score=s.score,
                band=s.band.value if s.band else None,
                reason=s.reason,
                model=s.model,
                prompt_version=s.prompt_version,
                created_at=s.created_at,
            )
            for s in scores
        ],
        related_news=related,
    )
    return out


@router.get("/{news_id}/analysis", summary="该资讯的分析报告")
async def get_news_analysis(news_id: str, session: SessionDep):
    news = await _get_news_by_public_id(session, news_id)
    report = (
        await session.execute(
            select(AnalysisReport)
            .where(
                AnalysisReport.news_id == news.id,
                AnalysisReport.status == ReportStatus.PUBLISHED,
            )
            .order_by(AnalysisReport.published_at.desc())
        )
    ).scalars().first()
    if report is None:
        raise NotFoundError("该资讯尚无分析报告")
    return {"id": str(report.public_id), "agent_type": report.agent_type.value, "title": report.title,
            "summary": report.summary, "content": report.content, "status": report.status.value}


# ----------------------------------------------------------------------
async def _get_news_by_public_id(session, public_id: str) -> NewsItem:
    from uuid import UUID

    try:
        uid = UUID(public_id)
    except ValueError as exc:
        raise NotFoundError("资讯不存在") from exc
    news = (
        await session.execute(select(NewsItem).where(NewsItem.public_id == uid))
    ).scalar_one_or_none()
    if news is None:
        raise NotFoundError("资讯不存在")
    return news


async def _public_ids(session, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await session.execute(select(NewsItem.id, NewsItem.public_id).where(NewsItem.id.in_(ids)))
    return {r[0]: str(r[1]) for r in rows.all()}


async def _latest_analysis(session, news_ids: list[int]) -> dict[int, AnalysisReport]:
    if not news_ids:
        return {}
    rows = await session.execute(
        select(AnalysisReport)
        .where(
            AnalysisReport.news_id.in_(news_ids),
            AnalysisReport.agent_type.in_(
                [AgentType.MACRO_POLICY, AgentType.INDUSTRY, AgentType.STOCK]
            ),
            AnalysisReport.status.in_([ReportStatus.PUBLISHED, ReportStatus.DEGRADED]),
        )
        .order_by(AnalysisReport.published_at.desc())
    )
    result: dict[int, AnalysisReport] = {}
    for report in rows.scalars().all():
        result.setdefault(report.news_id, report)  # type: ignore[index]
    return result


async def _entities(session, news_ids: list[int]) -> dict[int, list[EntityOut]]:
    if not news_ids:
        return {}
    rows = await session.execute(select(NewsEntity).where(NewsEntity.news_id.in_(news_ids)))
    mapping: dict[int, list[EntityOut]] = {}
    for e in rows.scalars().all():
        mapping.setdefault(e.news_id, []).append(
            EntityOut(
                type=e.entity_type.value if e.entity_type else "macro",
                code=e.code,
                name=e.name,
                confidence=e.confidence,
            )
        )
    return mapping


def _to_out(news: NewsItem, report: AnalysisReport | None, entities: list[EntityOut]) -> NewsItemOut:
    return NewsItemOut(
        id=str(news.public_id),
        title=news.title,
        summary=(news.content or "")[:120] or news.title,
        source=news.source,
        src=news.src,
        src_name=news.src_name,
        kind=news.kind.value if news.kind else None,
        channels=news.channels,
        publish_time=news.publish_time,
        ingested_at=news.ingested_at,
        score=news.score,
        band=news.band.value if news.band else None,
        score_reason=news.score_reason,
        tags=list(news.tags or []) if isinstance(news.tags, list) else [],
        entities=entities,
        has_analysis=report is not None,
        analysis_summary=report.summary if report else None,
        analysis_id=str(report.public_id) if report else None,
        seen_count=news.seen_count,
    )
