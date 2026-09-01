"""资讯域模型：news_item / news_score / news_chunk / news_entity。"""
from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from fin_news.core.config import get_settings
from fin_news.core.enums import EntityType, IngestKind, NewsStatus, ScoreBand
from fin_news.models.base import Base, PublicIdMixin, TimestampMixin, fk, pg_enum

VECTOR_DIM = get_settings().embedding_dim

score_band_t = pg_enum(ScoreBand, "score_band")
news_status_t = pg_enum(NewsStatus, "news_status")
ingest_kind_t = pg_enum(IngestKind, "ingest_kind")
entity_type_t = pg_enum(EntityType, "entity_type")


class NewsItem(Base, PublicIdMixin, TimestampMixin):
    """资讯主表：一条原始新闻 / 快讯 / 公告。"""

    __tablename__ = "news_item"
    __table_args__ = (
        Index("uq_news_dedup", "source_key", "content_hash", unique=True),
        Index("idx_news_simhash", "simhash"),
        Index("idx_news_publish_desc", "publish_time"),
        Index("idx_news_score_time", "score", "publish_time"),
        Index("idx_news_band_time", "band", "publish_time"),
        Index("idx_news_status_open", "status"),
        CheckConstraint("score IS NULL OR (score BETWEEN 1 AND 10)", name="ck_news_score_range"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ---- 来源 ----
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="tushare")
    source_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[IngestKind] = mapped_column(ingest_kind_t, nullable=False, default=IngestKind.NEWS)
    src: Mapped[str | None] = mapped_column(String(32))
    src_name: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(128))

    # ---- 内容 ----
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    content_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    channels: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(Text)

    # ---- 时间 ----
    publish_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # ---- 去重 ----
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    simhash: Mapped[int | None] = mapped_column(BigInteger)
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dedup_of: Mapped[int | None] = mapped_column(BigInteger)

    # ---- 评分（当前生效值） ----
    score: Mapped[int | None] = mapped_column(SmallInteger)
    band: Mapped[ScoreBand | None] = mapped_column(score_band_t)
    score_reason: Mapped[str | None] = mapped_column(Text)
    score_model: Mapped[str | None] = mapped_column(String(64))
    score_version: Mapped[str | None] = mapped_column(String(32))
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # ---- 处理状态 ----
    status: Mapped[NewsStatus] = mapped_column(news_status_t, nullable=False, default=NewsStatus.NEW)
    analysis_status: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    news_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class NewsScore(Base, TimestampMixin):
    """评分历史：可重算、可审计。"""

    __tablename__ = "news_score"
    __table_args__ = (
        Index("idx_score_news", "news_id", "created_at"),
        CheckConstraint("score BETWEEN 1 AND 10", name="ck_score_range"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    news_id: Mapped[int] = mapped_column(BigInteger, fk("news_item.id"), nullable=False)

    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    band: Mapped[ScoreBand] = mapped_column(score_band_t, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confidence: Mapped[float | None] = mapped_column()
    is_suspect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    batch_id: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class NewsChunk(Base, TimestampMixin):
    """向量分块（pgvector）。"""

    __tablename__ = "news_chunk"
    __table_args__ = (
        Index("uq_chunk", "news_id", "chunk_index", unique=True),
        Index(
            "idx_chunk_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("idx_chunk_news", "news_id"),
        Index("idx_chunk_entities", "entity_codes", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    news_id: Mapped[int] = mapped_column(BigInteger, fk("news_item.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(VECTOR_DIM), nullable=False)

    # 冗余字段：便于检索时单表过滤
    score: Mapped[int | None] = mapped_column(SmallInteger)
    band: Mapped[ScoreBand | None] = mapped_column(score_band_t)
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entity_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String(32)))
    model: Mapped[str] = mapped_column(String(64), nullable=False)


class NewsEntity(Base, TimestampMixin):
    """资讯关联的标的 / 板块 / 指数 / 宏观主体。"""

    __tablename__ = "news_entity"
    __table_args__ = (Index("idx_entity_news", "news_id"), Index("idx_entity_code", "code"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    news_id: Mapped[int] = mapped_column(BigInteger, fk("news_item.id"), nullable=False)

    entity_type: Mapped[EntityType] = mapped_column(entity_type_t, nullable=False)
    code: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column()
    extra: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), default=dict)
