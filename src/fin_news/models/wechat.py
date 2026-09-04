"""微信公众号文章域模型：wechat_article / wechat_article_chunk。

wechat_article_chunk 与 news_chunk 结构对齐（HALFVEC 向量 + HNSW 索引），
使历史文章可以被向量检索，支撑写文章 Agent 的「记忆与连续性」。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from fin_news.core.enums import ArticleStatus
from fin_news.models.base import Base, PublicIdMixin, TimestampMixin, fk, pg_enum
from fin_news.models.news import VECTOR_DIM, VECTOR_TYPE

article_status_t = pg_enum(ArticleStatus, "article_status")


class WechatArticle(Base, PublicIdMixin, TimestampMixin):
    """一篇公众号文章（正文 + 状态 + 溯源）。"""

    __tablename__ = "wechat_article"
    __table_args__ = (
        Index("idx_wechat_article_status", "status"),
        Index("idx_wechat_article_publish_date", "publish_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[ArticleStatus] = mapped_column(
        article_status_t, nullable=False, default=ArticleStatus.NEW
    )

    # 封面 / 配图：由「图片生成」工具型 skill 后续产出（本期只留字段）
    cover_image: Mapped[str | None] = mapped_column(Text)
    cover_hint: Mapped[str | None] = mapped_column(Text)  # Agent 对封面的文字建议（供封面 skill 使用）
    images: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # 文章对应的交易日/日期
    publish_date: Mapped[date] = mapped_column(Date, nullable=False)

    # 溯源：引用的资讯 id 与历史文章 id（用于「我之前文章里讲过 xx」与数据回查）
    source_news_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    referenced_article_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # 生成元信息（审计）
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    # 手动置为 PUBLISHED 的时间
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WechatArticleChunk(Base, TimestampMixin):
    """文章分块向量（pgvector），供历史文章检索。"""

    __tablename__ = "wechat_article_chunk"
    __table_args__ = (
        Index("uq_wechat_article_chunk", "article_id", "chunk_index", unique=True),
        Index(
            "idx_wechat_article_chunk_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
        Index("idx_wechat_article_chunk_article", "article_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(
        BigInteger, fk("wechat_article.id"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(VECTOR_TYPE(VECTOR_DIM), nullable=False)

    model: Mapped[str] = mapped_column(String(64), nullable=False)
