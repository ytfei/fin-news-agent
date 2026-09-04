"""文章检索工具：向量检索历史公众号文章（只查已发布 PUBLISHED）。

这是写文章 Agent 的「记忆」：写新文章前，先回顾自己已发布过的历史文章，
可引用「我之前的文章里讲过 xx」，避免重复讲解。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.core.enums import ArticleStatus
from fin_news.core.logging import get_logger
from fin_news.models.wechat import WechatArticle, WechatArticleChunk

logger = get_logger("agents.tools.article_retrieval")


@dataclass
class ArticleHit:
    article_id: int
    public_id: str
    chunk_id: int
    title: str
    snippet: str
    publish_date: date | None
    similarity: float


async def embed_query(text: str) -> list[float]:
    from fin_news.agents.tools.retrieval import embed_query as _embed

    return await _embed(text)


def _article_search_stmt(vector: list[float], top_k: int):
    """构建历史文章检索语句（join 文章主表，强制 status == PUBLISHED）。"""
    distance = WechatArticleChunk.embedding.cosine_distance(vector)  # type: ignore[arg-type]
    return (
        select(
            WechatArticleChunk.id,
            WechatArticleChunk.article_id,
            WechatArticleChunk.content,
            WechatArticle.title,
            WechatArticle.publish_date,
            WechatArticle.public_id,
            distance.label("distance"),
        )
        .join(WechatArticle, WechatArticle.id == WechatArticleChunk.article_id)
        .where(WechatArticle.status == ArticleStatus.PUBLISHED)
        .order_by(distance.asc())
        .limit(top_k)
    )


async def article_search(
    session: AsyncSession,
    query: str,
    *,
    top_k: int = 8,
) -> list[ArticleHit]:
    """按语义检索历史已发布文章，返回带相似度的片段。

    SQL 层强制 `status == PUBLISHED`，从数据上保证「只能查已发布的历史文章」，
    不依赖 Agent 自觉。
    """
    vector = await embed_query(query)
    if not vector:
        return []

    stmt = _article_search_stmt(vector, top_k)
    rows = (await session.execute(stmt)).all()
    return [
        ArticleHit(
            article_id=r.article_id,
            public_id=str(r.public_id),
            chunk_id=r.id,
            title=r.title,
            snippet=(r.content or "")[:300],
            publish_date=r.publish_date,
            similarity=round(1.0 - float(r.distance or 1.0), 4),
        )
        for r in rows
    ]


def format_article_hits(hits: list[ArticleHit]) -> str:
    """把检索结果格式化成可塞进 prompt 的上下文。"""
    if not hits:
        return "（未检索到相关历史文章）"
    lines = []
    for idx, hit in enumerate(hits, start=1):
        date_str = hit.publish_date.isoformat() if hit.publish_date else "时间未知"
        lines.append(
            f"[{idx}] （{date_str}，相似度{hit.similarity:.2f}）《{hit.title}》\n    {hit.snippet}"
        )
    return "\n".join(lines)
