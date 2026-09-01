"""接入编排：一次增量任务 = 取位点 → 拉取 → 归一化 → 过滤 → 去重 → 落库 → 发事件 → 前进位点。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.core.config import Settings, get_settings
from fin_news.core.db import session_scope
from fin_news.core.enums import EventType
from fin_news.core.logging import get_logger
from fin_news.core.timeutil import now
from fin_news.domain.schemas import IngestResult
from fin_news.events.bus import EventBus
from fin_news.ingestion.cursor import CursorManager
from fin_news.ingestion.deduper import Deduper
from fin_news.ingestion.normalizer import normalize_batch
from fin_news.ingestion.rule_filter import filter_reason
from fin_news.ingestion.sources.base import NewsSource
from fin_news.ingestion.sources.tushare_news import build_news_sources
from fin_news.ingestion.tushare_client import TushareError
from fin_news.models.analysis import IngestCursor
from fin_news.models.news import NewsItem

logger = get_logger("ingestion.service")

MAX_WINDOW = timedelta(hours=24)


class IngestionService:
    def __init__(
        self,
        sources: list[NewsSource] | None = None,
        settings: Settings | None = None,
        worker_id: str = "ingest",
    ) -> None:
        self.settings = settings or get_settings()
        self.sources = sources if sources is not None else build_news_sources(settings=self.settings)
        self.worker_id = worker_id

    # ------------------------------------------------------------------
    async def run_all(self) -> list[IngestResult]:
        results: list[IngestResult] = []
        for source in self.sources:
            try:
                results.append(await self.run_source(source))
            except Exception as exc:  # noqa: BLE001 - 单源失败不影响其他源
                logger.exception("数据源运行异常", source_key=source.source_key, error=str(exc))
                results.append(
                    IngestResult(
                        source_key=source.source_key, status="FAILED", message=str(exc)[:500]
                    )
                )
        return results

    async def run_source(self, source: NewsSource) -> IngestResult:
        ran_at = now()
        async with session_scope() as session:
            cursors = CursorManager(session)
            default_time = ran_at - timedelta(hours=self.settings.ingest_first_lookback_hours)
            cursor = await cursors.get_or_create(
                source.source_key,
                default_time=default_time,
                kind=source.meta.kind,
                overlap_seconds=self.settings.ingest_overlap_seconds,
            )
            if not cursor.enabled:
                await cursors.mark_skipped(cursor, "数据源已禁用")
                return IngestResult(source_key=source.source_key, status="SKIPPED", message="数据源已禁用")

            since, until = self._window(cursor)
            try:
                raw_items = await source.fetch(since, until)
            except TushareError as exc:
                await cursors.mark_failure(cursor, str(exc), ran_at)
                if "权限" in str(exc):
                    cursor.enabled = False
                return IngestResult(
                    source_key=source.source_key, status="FAILED", message=str(exc)[:500]
                )

            result = await self._persist(session, source, raw_items, ran_at)

            # 只有整轮成功才前进位点
            if result.status == "OK":
                await cursors.mark_success(cursor, until, result.inserted, ran_at)
            return result

    # ------------------------------------------------------------------
    def _window(self, cursor: IngestCursor) -> tuple[datetime, datetime]:
        until = now()
        since = cursor.cursor_time - timedelta(seconds=cursor.overlap_seconds)
        if until - since > MAX_WINDOW:
            logger.warning(
                "拉取窗口过大，已截断为 24h", source_key=cursor.source_key, since=since.isoformat()
            )
            since = until - MAX_WINDOW
        return since, until

    async def _persist(
        self,
        session: AsyncSession,
        source: NewsSource,
        raw_items: list,
        ran_at: datetime,
    ) -> IngestResult:
        result = IngestResult(source_key=source.source_key, fetched=len(raw_items))

        normalized = normalize_batch(
            raw_items, source.source_key, source.meta.src, source.meta.src_name
        )

        # 规则层噪声过滤
        kept = []
        for item in normalized:
            reason = filter_reason(item)
            if reason:
                result.filtered += 1
            else:
                kept.append(item)

        # 去重
        deduper = Deduper(session, self.settings)
        fresh, duplicates = await deduper.filter_new(kept)
        result.duplicates = duplicates

        if fresh:
            inserted = await self._bulk_insert(session, fresh, ran_at)
            result.inserted = len(inserted)
            # 事件与数据同事务提交，避免「事件先于数据」
            bus = EventBus(session, worker_id=self.worker_id)
            for news_id in inserted:
                await bus.publish(EventType.NEWS_INGESTED, news_id)

        logger.info(
            "接入完成",
            source_key=source.source_key,
            fetched=result.fetched,
            inserted=result.inserted,
            duplicates=result.duplicates,
            filtered=result.filtered,
        )
        return result

    async def _bulk_insert(
        self, session: AsyncSession, items: list, ran_at: datetime
    ) -> list[int]:
        rows = [
            {
                "source": item.source,
                "source_key": item.source_key,
                "kind": item.kind,
                "src": item.src,
                "src_name": item.src_name,
                "external_id": item.external_id,
                "title": item.title,
                "content": item.content,
                "content_truncated": item.content_truncated,
                "channels": item.channels,
                "url": item.url,
                "publish_time": item.publish_time,
                "ingested_at": ran_at,
                "first_seen_at": ran_at,
                "content_hash": item.content_hash,
                "simhash": item.simhash,
                "seen_count": 1,
                "status": "NEW",
                "analysis_status": "NONE",
                "retry_count": 0,
                "tags": [],
                # 注意：必须使用模型属性名 news_metadata，
                # "metadata" 会被解析成 DeclarativeBase.metadata（注册表）
                "news_metadata": item.metadata,
            }
            for item in items
        ]
        stmt = (
            pg_insert(NewsItem)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["source_key", "content_hash"])
            .returning(NewsItem.id)
        )
        result = await session.execute(stmt)
        return [int(r[0]) for r in result.all()]
