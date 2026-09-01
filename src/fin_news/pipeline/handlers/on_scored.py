"""news.scored：score > 3 才做分块 + 向量化入库；否则归档为噪声。"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.embeddings import DimensionMismatch, get_embedder
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import EventType, NewsStatus
from fin_news.core.logging import get_logger
from fin_news.domain.chunking import chunk_text
from fin_news.domain.scoring import band_for_score, should_vectorize
from fin_news.domain.textutil import estimate_tokens
from fin_news.events.bus import EventBus
from fin_news.models.event import IngestEvent
from fin_news.models.news import NewsChunk, NewsEntity, NewsItem

logger = get_logger("pipeline.on_scored")


async def handle(
    session: AsyncSession,
    events: list[IngestEvent],
    bus: EventBus,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    news_ids = [e.aggregate_id for e in events]

    rows = await session.execute(
        select(NewsItem).where(
            NewsItem.id.in_(news_ids),
            NewsItem.status.in_([NewsStatus.SCORED, NewsStatus.EMBED_FAILED]),
        )
    )
    items = {n.id: n for n in rows.scalars().all()}

    for event in events:
        news = items.get(event.aggregate_id)
        if news is None:
            await bus.ack(event)
            continue

        if not should_vectorize(news.score, settings.score_threshold_vectorize):
            news.status = NewsStatus.ARCHIVED_NOISE
            news.analysis_status = "NONE"
            await bus.ack(event)
            continue

        if not settings.has_llm_credentials():
            logger.warning("未配置模型 API Key，跳过向量化", news_id=news.id)
            await bus.release(event)
            continue

        try:
            await vectorize_news(session, news, settings)
            news.status = NewsStatus.EMBEDDED
            await bus.publish(
                EventType.NEWS_EMBEDDED,
                news.id,
                payload={"score": news.score, "band": news.band.value if news.band else None},
                priority=2,
            )
            await bus.ack(event)
        except DimensionMismatch as exc:
            await session.rollback()
            logger.error("向量维度不匹配，终止入库", news_id=news.id, error=str(exc))
            raise
        except Exception as exc:  # noqa: BLE001
            news.status = NewsStatus.EMBED_FAILED
            news.retry_count = (news.retry_count or 0) + 1
            news.last_error = str(exc)[:500]
            await bus.fail(event, str(exc)[:300], error_type="EmbeddingFailed")


async def vectorize_news(session: AsyncSession, news: NewsItem, settings: Settings) -> int:
    """分块 → 批量 embedding → 幂等写入（先删后插）。

    公开给运维命令（cli embed）复用，保证手动补数与事件驱动走完全一致的逻辑。
    """
    embedder = get_embedder(settings)
    prefix = f"【{news.src_name or news.src or '资讯'}】{news.publish_time:%Y-%m-%d %H:%M} {news.title}\n"
    chunks = chunk_text(
        news.content or news.title or "",
        max_tokens=600,
        overlap_tokens=80,
        prefix=prefix,
    )
    if not chunks:
        return 0

    vectors = await embedder.embed(chunks)

    # 幂等：同一条资讯重跑时先清理旧分块
    await session.execute(delete(NewsChunk).where(NewsChunk.news_id == news.id))

    entity_rows = await session.execute(
        select(NewsEntity.code).where(NewsEntity.news_id == news.id, NewsEntity.code.is_not(None))
    )
    entity_codes = [c for (c,) in entity_rows.all()]
    band = news.band or band_for_score(news.score or 0)

    for idx, (text, vector) in enumerate(zip(chunks, vectors, strict=False)):
        session.add(
            NewsChunk(
                news_id=news.id,
                chunk_index=idx,
                content=text,
                token_count=estimate_tokens(text),
                embedding=vector,
                score=news.score,
                band=band,
                publish_time=news.publish_time,
                entity_codes=entity_codes or None,
                model=settings.model_for(settings.embedding_provider, "embedding"),  # type: ignore[arg-type]
            )
        )
    await session.flush()
    logger.debug("向量化完成", news_id=news.id, chunks=len(chunks))
    return len(chunks)
