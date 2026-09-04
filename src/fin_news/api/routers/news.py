"""资讯接口：列表 / 渠道聚合 / 详情 / 关联分析 / 相似资讯。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import ColumnElement, exists, func, or_, select

from fin_news.api.deps import PaginationDep, SessionDep
from fin_news.api.errors import NotFoundError
from fin_news.api.reporting import (
    NEWS_ANALYSIS_AGENTS,
    VISIBLE_REPORT_STATUS,
    band_rank,
    order_by_rank,
)
from fin_news.api.schemas import (
    EntityOut,
    NewsDetailOut,
    NewsItemOut,
    NewsSourceOut,
    Page,
    RelatedNewsOut,
    ScoreHistoryOut,
)
from fin_news.core.enums import ScoreBand
from fin_news.core.timeutil import now
from fin_news.models.analysis import AnalysisReport
from fin_news.models.news import NewsEntity, NewsItem, NewsScore

router = APIRouter(prefix="/news", tags=["news"])


def _analysis_exists() -> ColumnElement[bool]:
    """该资讯是否存在「可见的深度分析报告」。

    以 AnalysisReport 实表为唯一真实来源，不再依赖 news_item.analysis_status —— 该字段
    只在失败路径写入 FAILED，成功路径未维护，与报告表可能不一致。
    """
    return exists(
        select(AnalysisReport.id).where(
            AnalysisReport.news_id == NewsItem.id,
            AnalysisReport.agent_type.in_(NEWS_ANALYSIS_AGENTS),
            AnalysisReport.status.in_(VISIBLE_REPORT_STATUS),
        )
    ).correlate(NewsItem)


def _order_keys(sort: str, order: str) -> list[ColumnElement]:
    """排序键（含二级键）。

    末位追加主键：保证同分 / 同时刻的记录顺序完全确定，offset 分页不会重复或漏数据。
    """
    if sort == "impact":
        keys: list[ColumnElement] = [
            band_rank(NewsItem.band),  # 重要程度：宏观 > 行业 > 个股 > 噪声
            NewsItem.score,
            NewsItem.publish_time,
        ]
    elif sort == "score":
        keys = [NewsItem.score, NewsItem.publish_time]
    else:
        keys = [NewsItem.publish_time]
    keys.append(NewsItem.id)
    return order_by_rank(keys, order)


def _build_filters(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    band: list[ScoreBand] | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    source: list[str] | None = None,
    q: str | None = None,
    code: str | None = None,
    has_analysis: bool | None = None,
    default_since: datetime | None = None,
) -> list[ColumnElement]:
    """列表与渠道聚合共用的过滤条件。

    时间语义：给了 start 就按 start 起，给了 end 就按 end 止，两者都没给才回落到
    default_since（近 N 小时）。
    """
    conds: list[ColumnElement] = []

    if start is not None:
        conds.append(NewsItem.publish_time >= start)
    elif default_since is not None:
        conds.append(NewsItem.publish_time >= default_since)
    if end is not None:
        conds.append(NewsItem.publish_time <= end)

    if band:
        conds.append(NewsItem.band.in_(band))
    if min_score is not None:
        conds.append(NewsItem.score >= min_score)
    if max_score is not None:
        conds.append(NewsItem.score <= max_score)
    if source:
        conds.append(NewsItem.src.in_(source))
    if q:
        pattern = f"%{q}%"
        conds.append(or_(NewsItem.title.ilike(pattern), NewsItem.content.ilike(pattern)))
    if code:
        conds.append(NewsItem.id.in_(select(NewsEntity.news_id).where(NewsEntity.code == code)))
    if has_analysis is not None:
        cond = _analysis_exists()
        conds.append(cond if has_analysis else ~cond)

    return conds


@router.get("", response_model=Page[NewsItemOut], summary="资讯流")
async def list_news(
    session: SessionDep,
    pagination: PaginationDep,
    band: list[ScoreBand] | None = Query(default=None),
    min_score: int | None = Query(default=None, ge=1, le=10),
    max_score: int | None = Query(default=None, ge=1, le=10),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    source: list[str] | None = Query(default=None, description="渠道标识 cls / wallstreetcn"),
    q: str | None = Query(default=None, description="标题关键词"),
    code: str | None = Query(default=None, description="关联标的代码"),
    has_analysis: bool | None = Query(default=None),
    sort: str = Query(default="publish_time", pattern="^(publish_time|score|impact)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
):
    conds = _build_filters(
        start=start,
        end=end,
        band=band,
        min_score=min_score,
        max_score=max_score,
        source=source,
        q=q,
        code=code,
        has_analysis=has_analysis,
        default_since=now() - timedelta(days=1),  # 未指定时间范围时取近 24 小时
    )

    stmt = select(NewsItem)
    count_stmt = select(func.count()).select_from(NewsItem)
    for cond in conds:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    items = (
        (await session.execute(
            stmt.order_by(*_order_keys(sort, order)).offset(pagination.offset).limit(pagination.page_size)
        ))
        .scalars()
        .all()
    )

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


@router.get("/sources", response_model=list[NewsSourceOut], summary="渠道聚合（含各渠道条数）")
async def list_news_sources(
    session: SessionDep,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    band: list[ScoreBand] | None = Query(default=None),
    min_score: int | None = Query(default=None, ge=1, le=10),
    has_analysis: bool | None = Query(default=None),
):
    """资讯流顶部的渠道标签及其条数。

    过滤参数与 GET /news 对齐（不含分页 / 排序），保证「标签上的数字」与「切到该标签
    后列表的实际条数」一致。
    """
    conds = _build_filters(
        start=start,
        end=end,
        band=band,
        min_score=min_score,
        has_analysis=has_analysis,
        default_since=now() - timedelta(days=1),
    )
    # src 为空的记录无法归类到任何渠道标签，直接排除
    conds.append(NewsItem.src.is_not(None))

    rows = (
        await session.execute(
            select(NewsItem.src, func.max(NewsItem.src_name), func.count())
            .where(*conds)
            .group_by(NewsItem.src)
            .order_by(func.count().desc(), NewsItem.src.asc())
        )
    ).all()
    return [NewsSourceOut(src=r[0], src_name=r[1], count=int(r[2] or 0)) for r in rows]


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
                AnalysisReport.agent_type.in_(NEWS_ANALYSIS_AGENTS),
                AnalysisReport.status.in_(VISIBLE_REPORT_STATUS),
            )
            .order_by(AnalysisReport.published_at.desc().nullslast())
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
            AnalysisReport.agent_type.in_(NEWS_ANALYSIS_AGENTS),
            AnalysisReport.status.in_(VISIBLE_REPORT_STATUS),
        )
        .order_by(AnalysisReport.published_at.desc().nullslast())
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
        summary=_ellipsis((news.content or "").strip(), 120) or news.title,
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


def _ellipsis(text: str, limit: int) -> str:
    """按字符数截断并补省略号（中文场景不按字节切，避免半个字）。"""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"
