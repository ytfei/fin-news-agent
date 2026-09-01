"""检索工具：向量检索历史资讯（Agent 的「记忆」）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.core.enums import ScoreBand
from fin_news.core.logging import get_logger
from fin_news.domain.schemas import SearchHit
from fin_news.models.news import NewsChunk, NewsItem

logger = get_logger("agents.tools.retrieval")


async def embed_query(text: str) -> list[float]:
    from fin_news.agents.embeddings import get_embedder

    return await get_embedder().embed_one(text)


async def history_search(
    session: AsyncSession,
    query: str,
    *,
    top_k: int = 8,
    start: datetime | None = None,
    end: datetime | None = None,
    bands: list[ScoreBand] | None = None,
    min_score: int | None = None,
    codes: list[str] | None = None,
    exclude_news_id: int | None = None,
) -> list[SearchHit]:
    """按语义检索历史资讯，返回带相似度与引用的片段。"""
    vector = await embed_query(query)
    if not vector:
        return []

    distance = NewsChunk.embedding.cosine_distance(vector)  # type: ignore[arg-type]
    stmt = (
        select(
            NewsChunk.id,
            NewsChunk.news_id,
            NewsChunk.content,
            NewsChunk.publish_time,
            NewsChunk.score,
            NewsChunk.band,
            NewsItem.title,
            distance.label("distance"),
        )
        .join(NewsItem, NewsItem.id == NewsChunk.news_id)
        .order_by(distance.asc())
        .limit(top_k)
    )

    if start is not None:
        stmt = stmt.where(NewsChunk.publish_time >= start)
    if end is not None:
        stmt = stmt.where(NewsChunk.publish_time <= end)
    if bands:
        stmt = stmt.where(NewsChunk.band.in_(bands))
    if min_score is not None:
        stmt = stmt.where(NewsChunk.score >= min_score)
    if codes:
        stmt = stmt.where(NewsChunk.entity_codes.contains(codes))
    if exclude_news_id is not None:
        stmt = stmt.where(NewsChunk.news_id != exclude_news_id)

    rows = (await session.execute(stmt)).all()
    return [
        SearchHit(
            news_id=r.news_id,
            chunk_id=r.id,
            title=r.title,
            snippet=(r.content or "")[:300],
            publish_time=r.publish_time,
            score=r.score,
            band=r.band,
            similarity=round(1.0 - float(r.distance or 1.0), 4),
        )
        for r in rows
    ]


async def related_news(
    session: AsyncSession,
    news_id: int,
    *,
    limit: int = 10,
    start: datetime | None = None,
) -> list[SearchHit]:
    """找出与某条资讯语义相近的历史资讯（用于「历史上发生过类似事件」）。"""
    row = await session.execute(
        select(NewsChunk.embedding).where(NewsChunk.news_id == news_id).order_by(NewsChunk.chunk_index).limit(1)
    )
    vector = row.scalar_one_or_none()
    if vector is None:
        return []

    distance = NewsChunk.embedding.cosine_distance(vector)  # type: ignore[arg-type]
    stmt = (
        select(
            NewsChunk.id,
            NewsChunk.news_id,
            NewsChunk.content,
            NewsChunk.publish_time,
            NewsChunk.score,
            NewsChunk.band,
            NewsItem.title,
            distance.label("distance"),
        )
        .join(NewsItem, NewsItem.id == NewsChunk.news_id)
        .where(NewsChunk.news_id != news_id)
        .order_by(distance.asc())
        .limit(limit)
    )
    if start is not None:
        stmt = stmt.where(NewsChunk.publish_time >= start)

    rows = (await session.execute(stmt)).all()
    return [
        SearchHit(
            news_id=r.news_id,
            chunk_id=r.id,
            title=r.title,
            snippet=(r.content or "")[:300],
            publish_time=r.publish_time,
            score=r.score,
            band=r.band,
            similarity=round(1.0 - float(r.distance or 1.0), 4),
        )
        for r in rows
    ]


def format_hits(hits: list[SearchHit]) -> str:
    """把检索结果格式化成可塞进 prompt 的上下文。"""
    if not hits:
        return "（未检索到相关历史资讯）"
    lines = []
    for idx, hit in enumerate(hits, start=1):
        time_str = hit.publish_time.strftime("%Y-%m-%d %H:%M") if hit.publish_time else "时间未知"
        score_str = f"评分{hit.score}" if hit.score is not None else "未评分"
        lines.append(f"[{idx}] ({time_str}，{score_str}，相似度{hit.similarity:.2f}) {hit.title}\n    {hit.snippet}")
    return "\n".join(lines)
