"""news.scored：score > 3 才做分块 + 向量化入库；否则归档为噪声。"""
from __future__ import annotations

import time

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.embeddings import DimensionMismatch, get_embedder
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import EventType, NewsStatus
from fin_news.core.logging import bind_context, elapsed_ms, get_logger, stage, unbind_context
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
    logger.info("向量化批次开始", events=len(events), news_ids=news_ids[:20])

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
            logger.debug("资讯不存在或状态已变更，直接确认", news_id=event.aggregate_id)
            await bus.ack(event)
            continue

        if not should_vectorize(news.score, settings.score_threshold_vectorize):
            news.status = NewsStatus.ARCHIVED_NOISE
            news.analysis_status = "NONE"
            logger.info(
                "评分未达阈值，归档为噪声",
                news_id=news.id,
                score=news.score,
                threshold=settings.score_threshold_vectorize,
            )
            await bus.ack(event)
            continue

        if not settings.has_llm_credentials():
            logger.warning("未配置模型 API Key，跳过向量化", news_id=news.id)
            await bus.release(event)
            continue

        # 绑定到上下文：向量化内部（含 embedding 调用）的日志自动带 news_id
        bind_context(news_id=news.id)
        started = time.perf_counter()
        try:
            logger.info("向量化开始", news_id=news.id, score=news.score, attempt=event.attempts + 1)
            chunks = await vectorize_news(session, news, settings)
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
            logger.error(
                "向量化失败",
                news_id=news.id,
                elapsed_ms=elapsed_ms(started),
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
            await bus.fail(event, str(exc)[:300], error_type="EmbeddingFailed")
        else:
            logger.info(
                "向量化完成", news_id=news.id, chunks=chunks, elapsed_ms=elapsed_ms(started)
            )
        finally:
            unbind_context("news_id")


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
        logger.info("分块结果为空，跳过向量化", news_id=news.id)
        return 0
    logger.debug("分块完成", news_id=news.id, chunks=len(chunks))

    async with stage(
        logger,
        "Embedding 调用",
        level="debug",
        news_id=news.id,
        chunks=len(chunks),
        provider=settings.embedding_provider,
        model=settings.model_for(settings.embedding_provider, "embedding"),
    ):
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
    logger.debug("分块已写入", news_id=news.id, chunks=len(chunks), entities=len(entity_codes))
    return len(chunks)
