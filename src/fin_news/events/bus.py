"""库内事件总线：与业务数据同库同事务，避免「数据已提交但事件丢失」。

消费使用 SELECT ... FOR UPDATE SKIP LOCKED，支持多 worker 并发且不重复。
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.core.config import get_settings
from fin_news.core.enums import EventStatus, EventType
from fin_news.core.logging import get_logger
from fin_news.events.types import EVENT_SPECS
from fin_news.models.event import DeadLetter, IngestEvent

logger = get_logger("events.bus")


def _now() -> datetime:
    return datetime.now(tz=UTC)


class EventBus:
    def __init__(self, session: AsyncSession, worker_id: str = "worker-1") -> None:
        self.session = session
        self.worker_id = worker_id

    # ------------------------------ 发布 ------------------------------
    async def publish(
        self,
        event_type: EventType | str,
        aggregate_id: int,
        payload: dict[str, Any] | None = None,
        priority: int | None = None,
        delay_seconds: int = 0,
        max_attempts: int | None = None,
    ) -> int | None:
        """返回新建事件 id；若同聚合同类型事件未处理完则返回 None（软去重）。"""
        """发布事件；同一聚合的同类型事件若仍未处理则忽略（软去重，天然幂等）。"""
        et = EventType(event_type) if isinstance(event_type, str) else event_type
        spec = EVENT_SPECS[et]
        settings = get_settings()

        values = {
            "event_type": et.value,
            "aggregate_type": spec.aggregate_type,
            "aggregate_id": aggregate_id,
            "payload": payload or {},
            "status": EventStatus.PENDING,
            "priority": priority if priority is not None else spec.priority,
            "available_at": _now() + timedelta(seconds=delay_seconds),
            "max_attempts": max_attempts or settings.event_max_attempts,
        }
        stmt = (
            pg_insert(IngestEvent)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["event_type", "aggregate_id"],
                # 必须与部分唯一索引 uq_event_pending_dedup 的谓词一致（OR 形式）
                index_where=sa_text("status = 'PENDING' OR status = 'PROCESSING'"),
            )
            .returning(IngestEvent.id)
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            logger.debug("事件已存在，跳过发布", event_type=et.value, aggregate_id=aggregate_id)
            return None
        return int(row[0])

    # ------------------------------ 消费 ------------------------------
    async def poll(self, limit: int | None = None) -> Sequence[IngestEvent]:
        """拉取并锁定一批待处理事件（行级锁，多 worker 安全）。"""
        settings = get_settings()
        limit = limit or settings.worker_batch_limit
        now = _now()

        subq = (
            select(IngestEvent.id)
            .where(
                IngestEvent.status == EventStatus.PENDING,
                IngestEvent.available_at <= now,
            )
            .order_by(IngestEvent.priority.desc(), IngestEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        ids = [row[0] for row in (await self.session.execute(subq)).all()]
        if not ids:
            return []

        # 注意：poll 不递增 attempts，只有 fail() 才递增，
        # 这样"攒批未处理被放回队列"不会消耗重试次数。
        stmt = (
            update(IngestEvent)
            .where(IngestEvent.id.in_(ids), IngestEvent.status == EventStatus.PENDING)
            .values(
                status=EventStatus.PROCESSING,
                locked_by=self.worker_id,
                locked_at=now,
            )
            .returning(IngestEvent)
        )
        result = await self.session.execute(stmt)
        events = result.scalars().all()
        if events:
            logger.debug("拉取事件", count=len(events))
        return events

    async def ack(self, event: IngestEvent | int) -> None:
        event_id = event if isinstance(event, int) else event.id
        await self.session.execute(
            update(IngestEvent)
            .where(IngestEvent.id == event_id)
            .values(status=EventStatus.DONE, processed_at=_now(), last_error=None, locked_by=None)
        )

    async def fail(self, event: IngestEvent | int, error: str, error_type: str = "HandlerError") -> None:
        """处理失败：可重试则退避后回退 PENDING，超过上限则进死信。"""
        event_id = event if isinstance(event, int) else event.id
        row = await self.session.execute(select(IngestEvent).where(IngestEvent.id == event_id))
        ev = row.scalar_one_or_none()
        if ev is None:
            return

        settings = get_settings()
        attempts = ev.attempts + 1
        if attempts >= ev.max_attempts:
            await self.session.execute(
                update(IngestEvent)
                .where(IngestEvent.id == event_id)
                .values(
                    status=EventStatus.FAILED,
                    last_error=error,
                    attempts=attempts,
                    processed_at=_now(),
                    locked_by=None,
                )
            )
            self.session.add(
                DeadLetter(
                    source_table="ingest_event",
                    source_id=str(ev.id),
                    event_type=ev.event_type,
                    error_type=error_type,
                    error_message=error,
                    payload={"aggregate_type": ev.aggregate_type, "aggregate_id": ev.aggregate_id, **ev.payload},
                    attempts=attempts,
                )
            )
            logger.warning("事件进入死信", event_id=event_id, event_type=ev.event_type, error=error[:200])
            return

        backoff = settings.event_backoff_base_seconds * (2 ** (attempts - 1))
        await self.session.execute(
            update(IngestEvent)
            .where(IngestEvent.id == event_id)
            .values(
                status=EventStatus.PENDING,
                attempts=attempts,
                last_error=error,
                available_at=_now() + timedelta(seconds=backoff),
                locked_by=None,
            )
        )
        logger.warning(
            "事件重试",
            event_id=event_id,
            event_type=ev.event_type,
            attempts=attempts,
            backoff=backoff,
            error=error[:200],
        )

    async def release(
        self,
        events: IngestEvent | int | Sequence[IngestEvent] | Sequence[int],
    ) -> None:
        """把事件放回队列（不消耗重试次数），用于攒批未处理或依赖未就绪的情况。

        同时支持传入单个事件 / 事件列表 / 事件 id（列表）。
        """
        if isinstance(events, (int, IngestEvent)):
            events = [events]
        ids = [e if isinstance(e, int) else e.id for e in events]
        if not ids:
            return
        await self.session.execute(
            update(IngestEvent)
            .where(IngestEvent.id.in_(ids), IngestEvent.status == EventStatus.PROCESSING)
            .values(status=EventStatus.PENDING, locked_by=None, locked_at=None)
        )

    async def reclaim_stale(self, minutes: int = 10) -> int:
        """回收异常退出遗留的 PROCESSING 事件。"""
        cutoff = _now() - timedelta(minutes=minutes)
        result = await self.session.execute(
            update(IngestEvent)
            .where(IngestEvent.status == EventStatus.PROCESSING, IngestEvent.locked_at < cutoff)
            .values(status=EventStatus.PENDING, locked_by=None, locked_at=None)
        )
        reclaimed = result.rowcount or 0
        if reclaimed:
            logger.warning("回收遗留事件", count=reclaimed, stale_minutes=minutes)
        return reclaimed

    async def backlog(self) -> dict[str, int]:
        """积压统计：pending / overdue / dead_letter。"""
        now = _now()
        from sqlalchemy import func

        pending = await self.session.scalar(
            select(func.count()).select_from(IngestEvent).where(IngestEvent.status == EventStatus.PENDING)
        )
        overdue = await self.session.scalar(
            select(func.count())
            .select_from(IngestEvent)
            .where(IngestEvent.status == EventStatus.PENDING, IngestEvent.available_at < now)
        )
        dead = await self.session.scalar(
            select(func.count()).select_from(DeadLetter).where(DeadLetter.resolved_at.is_(None))
        )
        return {
            "pending": int(pending or 0),
            "overdue": int(overdue or 0),
            "dead_letter": int(dead or 0),
        }

    async def cleanup_done(self, retention_days: int | None = None) -> int:
        """清理已完成的过期事件。"""
        from sqlalchemy import delete

        settings = get_settings()
        days = retention_days or settings.event_retention_days
        cutoff = _now() - timedelta(days=days)
        result = await self.session.execute(
            delete(IngestEvent).where(
                IngestEvent.status.in_([EventStatus.DONE, EventStatus.FAILED]),
                IngestEvent.processed_at.is_not(None),
                IngestEvent.processed_at < cutoff,
            )
        )
        deleted = result.rowcount or 0
        if deleted:
            logger.info("清理历史事件", deleted=deleted, retention_days=days)
        return deleted
