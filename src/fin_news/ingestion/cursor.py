"""增量位点管理：cursor 只在整轮成功后前进，保证故障时可从断点续传。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.core.enums import IngestKind
from fin_news.core.logging import get_logger
from fin_news.models.analysis import IngestCursor

logger = get_logger("ingestion.cursor")


class CursorManager:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(
        self,
        source_key: str,
        default_time: datetime,
        kind: IngestKind = IngestKind.NEWS,
        overlap_seconds: int = 300,
    ) -> IngestCursor:
        """取位点（加行锁，防止多实例并发拉取同一源）。

        用 ``nowait`` 快速失败：上游 ``run_all`` 已用 advisory lock 串行化，
        此处的行锁只是兜底，避免异常情况下无限等待。
        """
        result = await self.session.execute(
            select(IngestCursor)
            .where(IngestCursor.source_key == source_key)
            .with_for_update(nowait=True)
            .limit(1)
        )
        cursor = result.scalars().first()
        if cursor is not None:
            return cursor

        cursor = IngestCursor(
            source_key=source_key,
            kind=kind,
            cursor_time=default_time,
            overlap_seconds=overlap_seconds,
            enabled=True,
        )
        self.session.add(cursor)
        await self.session.flush()
        logger.info("初始化位点", source_key=source_key, cursor_time=default_time.isoformat())
        return cursor

    async def mark_success(
        self, cursor: IngestCursor, new_time: datetime, count: int, ran_at: datetime
    ) -> None:
        cursor.cursor_time = new_time
        cursor.last_run_at = ran_at
        cursor.last_success_at = ran_at
        cursor.last_status = "OK"
        cursor.last_count = count
        cursor.last_error = None

    async def mark_failure(self, cursor: IngestCursor, error: str, ran_at: datetime) -> None:
        """失败时位点不前进，只记录状态。"""
        cursor.last_run_at = ran_at
        cursor.last_status = "FAILED"
        cursor.last_error = error[:1000]
        logger.warning("接入失败，位点保持不变", source_key=cursor.source_key, error=error[:200])

    async def mark_skipped(self, cursor: IngestCursor, reason: str) -> None:
        cursor.last_status = "SKIPPED"
        cursor.last_error = reason[:500]
