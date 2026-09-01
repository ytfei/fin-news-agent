"""去重：批内去重 + 库内精确去重(content_hash) + 近似去重(simhash 汉明距离)。"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.core.config import Settings, get_settings
from fin_news.core.logging import get_logger
from fin_news.domain.schemas import NormalizedItem
from fin_news.domain.textutil import NEAR_DUP_THRESHOLD, hamming_distance
from fin_news.models.news import NewsItem

logger = get_logger("ingestion.deduper")


class Deduper:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    async def filter_new(self, items: list[NormalizedItem]) -> tuple[list[NormalizedItem], int]:
        """返回 (新条目, 重复数量)。"""
        if not items:
            return [], 0

        # 1) 批内按 content_hash 去重
        batch_new: list[NormalizedItem] = []
        batch_seen: set[str] = set()
        duplicates = 0
        for item in items:
            if item.content_hash in batch_seen:
                duplicates += 1
                continue
            batch_seen.add(item.content_hash)
            batch_new.append(item)

        # 2) 库内精确去重
        hashes = [i.content_hash for i in batch_new]
        rows = await self.session.execute(
            select(NewsItem.content_hash).where(NewsItem.content_hash.in_(hashes))
        )
        existing = set(rows.scalars().all())

        candidates: list[NormalizedItem] = []
        for item in batch_new:
            if item.content_hash in existing:
                duplicates += 1
                await self._bump_seen(item.content_hash)
            else:
                candidates.append(item)

        if not candidates:
            return [], duplicates

        # 3) 近似去重：与最近窗口内的 simhash 比较
        window_start = min(i.publish_time for i in candidates) - timedelta(hours=24)
        recent = await self.session.execute(
            select(NewsItem.simhash, NewsItem.content_hash)
            .where(
                NewsItem.publish_time >= window_start,
                NewsItem.simhash.is_not(None),
            )
            .order_by(NewsItem.publish_time.desc())
            .limit(2000)
        )
        recent_rows = [(r[0], r[1]) for r in recent.all() if r[0] is not None]

        final: list[NormalizedItem] = []
        for item in candidates:
            dup_of = self._find_near_duplicate(item.simhash, recent_rows)
            if dup_of:
                await self._bump_seen(dup_of)
                duplicates += 1
                continue
            final.append(item)
            recent_rows.append((item.simhash, item.content_hash))

        if duplicates:
            logger.info("去重完成", candidates=len(candidates), duplicates=duplicates, kept=len(final))
        return final, duplicates

    @staticmethod
    def _find_near_duplicate(
        sim: int, rows: list[tuple[int, str]], threshold: int = NEAR_DUP_THRESHOLD
    ) -> str | None:
        for other, content_hash in rows:
            if hamming_distance(sim, other) <= threshold:
                return content_hash
        return None

    async def _bump_seen(self, content_hash: str) -> None:
        """重复出现时累加 seen_count，保留最早一条。"""
        result = await self.session.execute(
            select(NewsItem).where(NewsItem.content_hash == content_hash).limit(1)
        )
        item = result.scalars().first()
        if item is not None:
            item.seen_count = (item.seen_count or 0) + 1
