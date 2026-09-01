"""检索与个股视图：语义搜索 / 个股档案 / 板块。"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import desc, select

from fin_news.agents.tools.market_data import stock_snapshot
from fin_news.agents.tools.retrieval import history_search
from fin_news.api.deps import PaginationDep, SessionDep
from fin_news.api.errors import NotFoundError, ServiceUnavailableError
from fin_news.api.schemas import Page, SearchHitOut, SearchRequest, SectorQuoteOut, StockProfileOut
from fin_news.core.enums import ReportStatus, ScoreBand
from fin_news.models.analysis import AnalysisReport, Sector, StockBasic
from fin_news.models.news import NewsItem

router = APIRouter(tags=["search", "stocks"])


@router.post("/search", response_model=list[SearchHitOut], summary="语义检索")
async def search(session: SessionDep, payload: SearchRequest):
    try:
        bands = [ScoreBand(b) for b in (payload.band or [])] or None
        hits = await history_search(
            session,
            payload.query,
            top_k=payload.top_k,
            start=payload.start,
            end=payload.end,
            bands=bands,
            min_score=payload.min_score,
            codes=payload.codes,
        )
    except Exception as exc:  # noqa: BLE001
        raise ServiceUnavailableError(f"检索服务不可用：{str(exc)[:200]}") from exc

    news_ids = [h.news_id for h in hits]
    id_map: dict[int, str] = {}
    if news_ids:
        rows = await session.execute(
            select(NewsItem.id, NewsItem.public_id).where(NewsItem.id.in_(news_ids))
        )
        id_map = {r[0]: str(r[1]) for r in rows.all()}

    analysis_map: dict[int, str] = {}
    if news_ids:
        rows = await session.execute(
            select(AnalysisReport.news_id, AnalysisReport.public_id)
            .where(
                AnalysisReport.news_id.in_(news_ids),
                AnalysisReport.status == ReportStatus.PUBLISHED,
            )
            .order_by(desc(AnalysisReport.published_at))
        )
        for news_id, public_id in rows.all():
            analysis_map.setdefault(news_id, str(public_id))

    return [
        SearchHitOut(
            news_id=id_map.get(h.news_id, ""),
            chunk_id=h.chunk_id,
            title=h.title,
            snippet=h.snippet,
            publish_time=h.publish_time,
            score=h.score,
            band=h.band.value if h.band else None,
            similarity=h.similarity,
            analysis_id=analysis_map.get(h.news_id),
        )
        for h in hits
    ]


@router.get("/stocks/{ts_code}", response_model=StockProfileOut, summary="个股档案")
async def get_stock(ts_code: str, session: SessionDep):
    data = await stock_snapshot(session, ts_code, days=60)
    basic = (
        await session.execute(select(StockBasic).where(StockBasic.ts_code == ts_code))
    ).scalar_one_or_none()
    if basic is None and data.get("valuation") is None:
        raise NotFoundError("未找到该股票的行情数据（请先同步 daily / daily_basic）")

    return StockProfileOut(
        ts_code=ts_code,
        name=data.get("name") or (basic.name if basic else None),
        industry=data.get("industry"),
        market=basic.market if basic else None,
        latest=data.get("valuation"),
        trend=data.get("trend", {}).get("bars", []),
    )


@router.get("/stocks/{ts_code}/analysis", response_model=Page, summary="个股相关分析")
async def get_stock_analysis(ts_code: str, session: SessionDep, pagination: PaginationDep):
    from fin_news.models.news import NewsEntity

    subq = select(NewsEntity.news_id).where(NewsEntity.code == ts_code)
    stmt = (
        select(AnalysisReport)
        .where(
            AnalysisReport.news_id.in_(subq),
            AnalysisReport.status.in_([ReportStatus.PUBLISHED, ReportStatus.DEGRADED]),
        )
        .order_by(desc(AnalysisReport.published_at))
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return Page(
        page=pagination.page,
        page_size=pagination.page_size,
        total=len(rows),
        has_more=len(rows) == pagination.page_size,
        items=[
            {
                "id": str(r.public_id),
                "agent_type": r.agent_type.value,
                "title": r.title,
                "summary": r.summary,
                "score": r.score,
                "band": r.band.value if r.band else None,
                "published_at": r.published_at,
            }
            for r in rows
        ],
    )


@router.get("/stocks/{ts_code}/news", response_model=Page, summary="个股相关资讯")
async def get_stock_news(
    ts_code: str,
    session: SessionDep,
    pagination: PaginationDep,
    days: int = Query(default=30, ge=1, le=365),
):
    from fin_news.core.timeutil import now
    from fin_news.models.news import NewsEntity

    subq = select(NewsEntity.news_id).where(NewsEntity.code == ts_code)
    since = now() - timedelta(days=days)
    stmt = (
        select(NewsItem)
        .where(NewsItem.id.in_(subq), NewsItem.publish_time >= since)
        .order_by(desc(NewsItem.publish_time))
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return Page(
        page=pagination.page,
        page_size=pagination.page_size,
        total=len(rows),
        has_more=len(rows) == pagination.page_size,
        items=[
            {
                "id": str(n.public_id),
                "title": n.title,
                "src": n.src,
                "src_name": n.src_name,
                "publish_time": n.publish_time,
                "score": n.score,
                "band": n.band.value if n.band else None,
            }
            for n in rows
        ],
    )


@router.get("/sectors", response_model=list[SectorQuoteOut], summary="板块列表")
async def list_sectors(
    session: SessionDep,
    date_: date | None = Query(default=None, alias="date"),
):
    rows = (await session.execute(select(Sector).limit(500))).scalars().all()
    return [SectorQuoteOut(code=s.code, name=s.name, pct_chg=None) for s in rows]
