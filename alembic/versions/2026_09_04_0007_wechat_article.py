"""wechat article and chunk

新增微信公众号文章主表 wechat_article 与分块向量表 wechat_article_chunk。
wechat_article_chunk 复用 news_chunk 的 halfvec(2048) + HNSW 索引方案，
使历史文章可被向量检索（写文章 Agent 的「记忆与连续性」）。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects import postgresql

from fin_news.core.config import get_settings

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VECTOR_DIM = get_settings().embedding_dim

article_status = postgresql.ENUM(
    "NEW", "DRAFT", "PUBLISHED", "DELETED", name="article_status", create_type=False
)


def upgrade() -> None:
    article_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "wechat_article",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", article_status, nullable=False),
        sa.Column("cover_image", sa.Text(), nullable=True),
        sa.Column("cover_hint", sa.Text(), nullable=True),
        sa.Column("images", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("publish_date", sa.Date(), nullable=False),
        sa.Column("source_news_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("referenced_article_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index("idx_wechat_article_status", "wechat_article", ["status"], unique=False)
    op.create_index("idx_wechat_article_publish_date", "wechat_article", ["publish_date"], unique=False)

    op.create_table(
        "wechat_article_chunk",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("embedding", HALFVEC(VECTOR_DIM), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["wechat_article.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_wechat_article_chunk", "wechat_article_chunk", ["article_id", "chunk_index"], unique=True
    )
    op.create_index(
        "idx_wechat_article_chunk_embedding",
        "wechat_article_chunk",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": "16", "ef_construction": "64"},
        postgresql_ops={"embedding": "halfvec_cosine_ops"},
    )
    op.create_index("idx_wechat_article_chunk_article", "wechat_article_chunk", ["article_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_wechat_article_chunk_article", table_name="wechat_article_chunk")
    op.drop_index("idx_wechat_article_chunk_embedding", table_name="wechat_article_chunk")
    op.drop_index("uq_wechat_article_chunk", table_name="wechat_article_chunk")
    op.drop_table("wechat_article_chunk")
    op.drop_index("idx_wechat_article_publish_date", table_name="wechat_article")
    op.drop_index("idx_wechat_article_status", table_name="wechat_article")
    op.drop_table("wechat_article")
    article_status.drop(op.get_bind(), checkfirst=True)
