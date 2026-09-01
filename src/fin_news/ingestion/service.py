"""接入编排：一次增量任务 = 取位点 → 拉取 → 归一化 → 过滤 → 去重 → 落库 → 发事件 → 前进位点。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from sqlalchemy import text
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

# 接入全局互斥锁：PostgreSQL advisory lock 的 key（由字符串稳定派生）。
# 后台调度（APScheduler）与手动 `cli ingest` 可能并发，两者都会对 IngestCursor
# 做 `SELECT ... FOR UPDATE`，若不串行化会在行锁上互相阻塞数分钟。
_ADVISORY_LOCK_KEY = "fin_news:ingest"


def _elapsed_ms(started: float) -> int:
    """耗时（毫秒），用于观察各环节性能。"""
    return int((time.perf_counter() - started) * 1000)


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
        started = time.perf_counter()
        logger.info(
            "增量接入开始",
            sources=[s.source_key for s in self.sources],
            lookback_hours=self.settings.ingest_first_lookback_hours,
            overlap_seconds=self.settings.ingest_overlap_seconds,
        )

        # 全局互斥：拿不到 advisory lock 说明有另一实例（后台调度/手动 CLI）正在接入，
        # 本轮直接跳过，避免在 IngestCursor 的 FOR UPDATE 行锁上互相长时间阻塞。
        async with session_scope() as lock_session:
            got_lock = (
                await lock_session.execute(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                    {"key": _ADVISORY_LOCK_KEY},
                )
            ).scalar()
            if not got_lock:
                logger.warning(
                    "另一接入实例正在运行，跳过本轮",
                    lock_key=_ADVISORY_LOCK_KEY,
                )
                return []

            try:
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
            finally:
                await lock_session.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": _ADVISORY_LOCK_KEY},
                )

        logger.info(
            "增量接入结束",
            sources=len(results),
            fetched=sum(r.fetched for r in results),
            inserted=sum(r.inserted for r in results),
            duplicates=sum(r.duplicates for r in results),
            filtered=sum(r.filtered for r in results),
            not_ok=[r.source_key for r in results if r.status != "OK"],
            elapsed_ms=_elapsed_ms(started),
        )
        return results

    async def run_source(self, source: NewsSource) -> IngestResult:
        ran_at = now()
        started = time.perf_counter()
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
                logger.warning(
                    "数据源已禁用，跳过",
                    source_key=source.source_key,
                    last_error=cursor.last_error,
                )
                return IngestResult(source_key=source.source_key, status="SKIPPED", message="数据源已禁用")

            since, until = self._window(cursor)
            prev_cursor = cursor.cursor_time
            logger.info(
                "接入开始",
                source_key=source.source_key,
                api=source.meta.api_name,
                cursor=prev_cursor.isoformat(),
                since=since.isoformat(),
                until=until.isoformat(),
                window_minutes=round((until - since).total_seconds() / 60, 1),
            )

            fetch_started = time.perf_counter()
            try:
                raw_items = await source.fetch(since, until)
            except TushareError as exc:
                await cursors.mark_failure(cursor, str(exc), ran_at)
                if "权限" in str(exc):
                    cursor.enabled = False
                    logger.error("数据源无权限，已自动禁用", source_key=source.source_key, error=str(exc)[:300])
                return IngestResult(
                    source_key=source.source_key, status="FAILED", message=str(exc)[:500]
                )
            logger.info(
                "拉取完成",
                source_key=source.source_key,
                fetched=len(raw_items),
                elapsed_ms=_elapsed_ms(fetch_started),
            )
            if not raw_items:
                logger.info("本轮无新数据", source_key=source.source_key, since=since.isoformat())

            result = await self._persist(session, source, raw_items, ran_at)

            # 只有整轮成功才前进位点
            if result.status == "OK":
                await cursors.mark_success(cursor, until, result.inserted, ran_at)
                logger.info(
                    "位点前进",
                    source_key=source.source_key,
                    from_time=prev_cursor.isoformat(),
                    to_time=until.isoformat(),
                )
            logger.info("接入结束", source_key=source.source_key, status=result.status,
                        elapsed_ms=_elapsed_ms(started))
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
        started = time.perf_counter()
        result = IngestResult(source_key=source.source_key, fetched=len(raw_items))

        # 1) 归一化（含标题兜底）
        normalized = normalize_batch(
            raw_items, source.source_key, source.meta.src, source.meta.src_name
        )
        title_derived = sum(1 for i in normalized if i.metadata.get("title_derived"))
        logger.info(
            "归一化完成",
            source_key=source.source_key,
            count=len(normalized),
            title_derived=title_derived,
            elapsed_ms=_elapsed_ms(started),
        )

        # 2) 规则层噪声过滤（按原因分类计数，便于回查规则是否误杀）
        kept = []
        reasons: dict[str, int] = {}
        for item in normalized:
            reason = filter_reason(item)
            if reason:
                result.filtered += 1
                reasons[reason] = reasons.get(reason, 0) + 1
            else:
                kept.append(item)
        if result.filtered:
            logger.info(
                "规则过滤",
                source_key=source.source_key,
                filtered=result.filtered,
                reasons=reasons,
                kept=len(kept),
            )

        # 3) 去重（分层统计）
        deduper = Deduper(session, self.settings)
        fresh, stats = await deduper.filter_new(kept)
        result.duplicates = stats.total
        logger.info(
            "去重完成",
            source_key=source.source_key,
            candidates=len(kept),
            new=len(fresh),
            dup_in_batch=stats.in_batch,
            dup_exact=stats.exact,
            dup_near=stats.near,
        )

        if fresh:
            # 4) 落库（upsert，幂等）
            insert_started = time.perf_counter()
            inserted = await self._bulk_insert(session, fresh, ran_at)
            result.inserted = len(inserted)
            logger.info(
                "落库完成",
                source_key=source.source_key,
                rows=len(inserted),
                elapsed_ms=_elapsed_ms(insert_started),
            )

            # 5) 发事件：与数据同事务提交，避免「事件先于数据」
            bus = EventBus(session, worker_id=self.worker_id)
            for news_id in inserted:
                await bus.publish(EventType.NEWS_INGESTED, news_id)
            logger.info(
                "事件已发布",
                source_key=source.source_key,
                # 注意：不能用 event=，structlog 把 event 作为消息本身的保留键
                event_type=EventType.NEWS_INGESTED.value,
                count=len(inserted),
            )
        else:
            logger.info("无新增资讯", source_key=source.source_key, candidates=len(kept))

        logger.info(
            "接入完成",
            source_key=source.source_key,
            fetched=result.fetched,
            inserted=result.inserted,
            duplicates=result.duplicates,
            filtered=result.filtered,
            elapsed_ms=_elapsed_ms(started),
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
