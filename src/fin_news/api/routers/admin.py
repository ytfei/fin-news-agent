"""运维接口：补数 / 重跑 / 积压查询 / 死信重放。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from fin_news.api.deps import SessionDep
from fin_news.api.errors import NotFoundError
from fin_news.api.schemas import BacklogOut
from fin_news.core.enums import EventStatus, EventType
from fin_news.core.logging import get_logger
from fin_news.core.timeutil import now
from fin_news.events.bus import EventBus
from fin_news.models.event import DeadLetter, IngestEvent

logger = get_logger("api.admin")

router = APIRouter(prefix="/admin", tags=["admin"])


class BackfillRequest(BaseModel):
    start: datetime
    end: datetime
    source_keys: list[str] | None = None


@router.post("/ingest/backfill", summary="指定区间补数", status_code=202)
async def backfill(session: SessionDep, payload: BackfillRequest):
    """把区间内的资讯重新拉取一次（去重逻辑会跳过已存在的条目）。"""
    from fin_news.ingestion.service import IngestionService

    service = IngestionService()
    results = []
    for source in service.sources:
        if payload.source_keys and source.source_key not in payload.source_keys:
            continue
        from fin_news.ingestion.cursor import CursorManager

        # 直接把位点回退到指定起点，下一轮调度会自动补数
        manager = CursorManager(session)
        cursor = await manager.get_or_create(
            source.source_key, default_time=payload.start, kind=source.meta.kind
        )
        cursor.cursor_time = payload.start
        cursor.enabled = True
        results.append({"source_key": source.source_key, "cursor_time": payload.start.isoformat()})
    logger.info("已回退位点等待补数", sources=results)
    return {"job_id": None, "sources": results}


@router.post("/news/{news_id}/rescore", summary="重算评分", status_code=202)
async def rescore(news_id: int, session: SessionDep):
    news = await _get_news_id(session, news_id)
    bus = EventBus(session)
    await bus.publish(EventType.NEWS_INGESTED, news, payload={"manual": True})
    return {"news_id": news, "queued": True}


@router.post("/news/{news_id}/reanalyze", summary="重跑深度分析", status_code=202)
async def reanalyze(news_id: int, session: SessionDep):
    news = await _get_news_id(session, news_id)
    bus = EventBus(session)
    await bus.publish(EventType.NEWS_EMBEDDED, news, payload={"manual": True})
    return {"news_id": news, "queued": True}


@router.get("/events/backlog", response_model=BacklogOut, summary="事件积压与死信统计")
async def backlog(session: SessionDep):
    bus = EventBus(session)
    data = await bus.backlog()

    rows = await session.execute(
        select(IngestEvent.event_type, func.count())
        .where(IngestEvent.status == EventStatus.PENDING)
        .group_by(IngestEvent.event_type)
    )
    return BacklogOut(
        pending=data["pending"],
        overdue=data["overdue"],
        overdue_sum=data["overdue_sum"],
        overdue_3m=data["overdue_3m"],
        overdue_5m=data["overdue_5m"],
        overdue_10m=data["overdue_10m"],
        dead_letter=data["dead_letter"],
        by_type={r[0]: int(r[1]) for r in rows.all()},
    )


@router.post("/dead-letter/{dl_id}/replay", summary="重放死信", status_code=202)
async def replay_dead_letter(dl_id: int, session: SessionDep):
    dl = (
        await session.execute(select(DeadLetter).where(DeadLetter.id == dl_id))
    ).scalar_one_or_none()
    if dl is None:
        raise NotFoundError("死信记录不存在")

    bus = EventBus(session)
    event_type = EventType(dl.event_type) if dl.event_type else EventType.NEWS_INGESTED
    aggregate_id = int((dl.payload or {}).get("aggregate_id") or 0)
    if not aggregate_id:
        raise NotFoundError("死信记录缺少 aggregate_id，无法重放")

    await bus.publish(event_type, aggregate_id, payload={"replay": True})
    dl.resolved_at = now()
    return {"dead_letter_id": dl_id, "queued": True}


async def _get_news_id(session, news_id: int) -> int:
    from fin_news.models.news import NewsItem

    exists = (
        await session.execute(select(NewsItem.id).where(NewsItem.id == news_id))
    ).scalar_one_or_none()
    if exists is None:
        raise NotFoundError("资讯不存在")
    return int(exists)
