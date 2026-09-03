"""news.scored：score > 3 才做分块 + 向量化入库；否则归档为噪声。

处理策略（两段式批处理）：
* 先把整批资讯纯函数分块（不占 DB 会话 / 网络），
* 再把全部 chunk 汇入 Embedder 的进程级并发闸门（embedding_concurrency）统一
  请求 —— 消除「逐条资讯串行 × 单条内部小并发」的墙钟放大，
* 最后按资讯串行落库（仅本地 DB 写，快），普通失败仅影响该条（可重试不中断批次），
  DimensionMismatch 仍整批回滚终止（防向量索引污染）。

事件驱动路径与 cli embed 复用本模块的 chunk_news / embed_news_batch /
build_chunk_rows / write_chunks，保证补数与实时处理逻辑一致。
"""
from __future__ import annotations

import asyncio
import time

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.embeddings import DimensionMismatch, get_embedder
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import EventType, NewsStatus
from fin_news.core.logging import bind_context, elapsed_ms, get_logger, unbind_context
from fin_news.domain.chunking import chunk_text
from fin_news.domain.scoring import band_for_score, should_vectorize
from fin_news.domain.textutil import estimate_tokens
from fin_news.events.bus import EventBus
from fin_news.models.event import IngestEvent
from fin_news.models.news import NewsChunk, NewsEntity, NewsItem

logger = get_logger("pipeline.on_scored")

# 与旧实现保持一致的分块参数
_CHUNK_MAX_TOKENS = 600
_CHUNK_OVERLAP_TOKENS = 80


# ----------------------------------------------------------------------
# 共享 helper（事件驱动与 cli embed 复用）
# ----------------------------------------------------------------------


def _chunk_prefix(news: NewsItem) -> str:
    return f"【{news.src_name or news.src or '资讯'}】{news.publish_time:%Y-%m-%d %H:%M} {news.title}\n"


def chunk_news(news: NewsItem, settings: Settings | None = None) -> list[str]:
    """纯函数分块（无 DB / 网络 IO），可安全地在并发前批量调用。"""
    settings = settings or get_settings()
    return chunk_text(
        news.content or news.title or "",
        max_tokens=_CHUNK_MAX_TOKENS,
        overlap_tokens=_CHUNK_OVERLAP_TOKENS,
        prefix=_chunk_prefix(news),
    )


async def embed_news_batch(
    tasks: list[tuple[NewsItem, list[str]]],
    settings: Settings | None = None,
) -> dict[int, list[list[float]] | BaseException]:
    """整批资讯受限并发向量化，不占用业务数据库会话。

    每条资讯一个协程，chunk 请求共享 Embedder 的进程级闸门
    （embedding_concurrency），把旧实现「逐条资讯串行」变成真正的批内并发。

    返回 news_id -> 向量列表 | 异常对象：普通异常由调用方做单条失败隔离；
    维度不匹配（DimensionMismatch）直接抛出，由调用方整批回滚终止，
    防止污染向量索引。审计日志（含 ERROR）在本函数结束前统一批量落库一次。

    注意：本函数内禁止 bind/unbind news_id —— logging contextvar 在并发
    协程之间会互相污染；带 news_id 的日志请放到串行落库阶段输出。
    """
    settings = settings or get_settings()
    if not tasks:
        return {}

    embedder = get_embedder(settings)

    try:
        outcomes = await asyncio.gather(
            *(embedder.embed(chunks, auto_flush=False) for _, chunks in tasks),
            return_exceptions=True,
        )
    finally:
        await embedder.flush_logs()

    results: dict[int, list[list[float]] | BaseException] = {}
    for (news, _), outcome in zip(tasks, outcomes):
        if isinstance(outcome, BaseException):
            if isinstance(outcome, DimensionMismatch):
                raise outcome  # 整体终止（审计日志已 flush）
            results[news.id] = outcome
        else:
            results[news.id] = outcome
    return results


def build_chunk_rows(
    news: NewsItem,
    chunks: list[str],
    vectors: list[list[float]],
    entity_codes: list[str] | None,
    settings: Settings | None = None,
) -> list[NewsChunk]:
    """把分块与向量构造成 NewsChunk ORM 行（不落库）。"""
    settings = settings or get_settings()
    band = news.band or band_for_score(news.score or 0)
    model = settings.model_for(settings.embedding_provider, "embedding")
    rows: list[NewsChunk] = []
    for idx, (text, vector) in enumerate(zip(chunks, vectors, strict=False)):
        rows.append(
            NewsChunk(
                news_id=news.id,
                chunk_index=idx,
                content=text,
                token_count=estimate_tokens(text),
                embedding=vector,
                score=news.score,
                band=band,
                publish_time=news.publish_time,
                entity_codes=entity_codes,
                model=model,  # type: ignore[arg-type]
            )
        )
    return rows


async def write_chunks(
    session: AsyncSession,
    news: NewsItem,
    chunks: list[str],
    vectors: list[list[float]],
    settings: Settings | None = None,
) -> int:
    """幂等落库：删旧 chunk → 查实体码 → 批量插新 chunk。返回 chunk 数。"""
    settings = settings or get_settings()
    await session.execute(delete(NewsChunk).where(NewsChunk.news_id == news.id))
    entity_rows = await session.execute(
        select(NewsEntity.code).where(NewsEntity.news_id == news.id, NewsEntity.code.is_not(None))
    )
    entity_codes = [c for (c,) in entity_rows.all()]
    rows = build_chunk_rows(news, chunks, vectors, entity_codes or None, settings)
    if rows:
        session.add_all(rows)
    await session.flush()
    logger.debug(
        "分块已写入",
        news_id=news.id,
        chunks=len(chunks),
        entities=len(entity_codes),
    )
    return len(chunks)


async def vectorize_news(session: AsyncSession, news: NewsItem, settings: Settings) -> int:
    """单条资讯向量化（分块 → embedding → 幂等写入）。

    公开给运维命令（cli embed）复用，保证手动补数与事件驱动走完全一致的逻辑；
    单条场景下审计日志即时落库（与旧版一致）。
    """
    settings = settings or get_settings()
    chunks = chunk_news(news, settings)
    if not chunks:
        logger.info("分块结果为空，跳过向量化", news_id=news.id)
        return 0
    logger.debug("分块完成", news_id=news.id, chunks=len(chunks))

    embedder = get_embedder(settings)
    vectors = await embedder.embed(chunks)  # auto_flush=True：单条即写审计
    return await write_chunks(session, news, chunks, vectors, settings)


# ----------------------------------------------------------------------
# 事件驱动入口
# ----------------------------------------------------------------------


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

    # ---- 阶段 0：批量预检（不存在/噪声直接确认；无凭据放回）----
    collect: list[tuple[IngestEvent, NewsItem]] = []
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

        collect.append((event, news))

    if not collect:
        return

    # ---- 阶段 1：纯函数分块全部资讯（无 DB / 网络 IO）----
    plan: list[tuple[IngestEvent, NewsItem, list[str]]] = []
    for event, news in collect:
        chunks = chunk_news(news, settings)
        plan.append((event, news, chunks))

    with_chunks = [(news, chunks) for _, news, chunks in plan if chunks]

    # ---- 阶段 2：整批受限并发 embedding（不占业务 session）----
    vectors_by_id: dict[int, list[list[float]] | BaseException] = {}
    if with_chunks:
        vectors_by_id = await embed_news_batch(with_chunks, settings)

    # ---- 阶段 3：按资讯串行落库与事件推进（仅本地 DB，快；失败逐条隔离）----
    for event, news, chunks in plan:
        bind_context(news_id=news.id)
        started = time.perf_counter()
        try:
            if not chunks:
                # 空正文：不产生分块，仍按成功处理（与 vectorize_news 返回 0 语义一致）
                news.status = NewsStatus.EMBEDDED
                await bus.publish(
                    EventType.NEWS_EMBEDDED,
                    news.id,
                    payload={"score": news.score, "band": news.band.value if news.band else None},
                    priority=2,
                )
                await bus.ack(event)
            else:
                outcome = vectors_by_id.get(news.id)
                if isinstance(outcome, BaseException):
                    raise outcome
                if outcome is None:
                    raise RuntimeError("embedding 结果缺失（内部错误）")
                logger.info(
                    "向量化开始",
                    news_id=news.id,
                    score=news.score,
                    chunks=len(chunks),
                    attempt=event.attempts + 1,
                )
                await write_chunks(session, news, chunks, outcome, settings)
                news.status = NewsStatus.EMBEDDED
                news.analysis_status = "PENDING"
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
                "向量化完成", news_id=news.id, chunks=len(chunks), elapsed_ms=elapsed_ms(started)
            )
        finally:
            unbind_context("news_id")
