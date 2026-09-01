"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from fin_news.core.config import get_settings

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VECTOR_DIM = get_settings().embedding_dim

# 枚举类型
score_band = postgresql.ENUM("NOISE", "STOCK", "INDUSTRY", "MACRO", name="score_band", create_type=False)
news_status = postgresql.ENUM(
    "NEW", "SCORING", "SCORED", "ARCHIVED_NOISE", "EMBEDDING", "EMBEDDED", "ANALYZING", "ANALYZED",
    "SCORE_FAILED", "EMBED_FAILED", "ANALYSIS_FAILED", "DEAD", name="news_status", create_type=False,
)
ingest_kind = postgresql.ENUM(
    "news", "major_news", "anns", "forecast", "top_list", "market", name="ingest_kind", create_type=False
)
entity_type = postgresql.ENUM("stock", "sector", "index", "macro", name="entity_type", create_type=False)
agent_type = postgresql.ENUM(
    "scoring", "macro_policy", "industry", "stock", "pre_market", "post_market", "qa",
    name="agent_type", create_type=False,
)
run_status = postgresql.ENUM(
    "PENDING", "RUNNING", "SUCCESS", "FAILED", "TIMEOUT", "CANCELLED", "DEAD",
    name="run_status", create_type=False,
)
event_status = postgresql.ENUM(
    "PENDING", "PROCESSING", "DONE", "FAILED", name="event_status", create_type=False
)
report_status = postgresql.ENUM(
    "DRAFT", "PUBLISHED", "DEGRADED", "SUPERSEDED", name="report_status", create_type=False
)
market_period = postgresql.ENUM("pre_market", "post_market", name="market_period", create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    for enum_ in (
        score_band, news_status, ingest_kind, entity_type, agent_type,
        run_status, event_status, report_status, market_period,
    ):
        enum_.create(op.get_bind(), checkfirst=True)

    # ---------------- news_item ----------------
    op.create_table(
        "news_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("kind", ingest_kind, nullable=False),
        sa.Column("src", sa.String(32), nullable=True),
        sa.Column("src_name", sa.String(64), nullable=True),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("content_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("channels", sa.String(64), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("publish_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("simhash", sa.BigInteger(), nullable=True),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("dedup_of", sa.BigInteger(), nullable=True),
        sa.Column("score", sa.SmallInteger(), nullable=True),
        sa.Column("band", score_band, nullable=True),
        sa.Column("score_reason", sa.Text(), nullable=True),
        sa.Column("score_model", sa.String(64), nullable=True),
        sa.Column("score_version", sa.String(32), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", news_status, nullable=False),
        sa.Column("analysis_status", sa.String(16), nullable=False, server_default="NONE"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.CheckConstraint("score IS NULL OR (score BETWEEN 1 AND 10)", name="ck_news_score_range"),
    )
    op.create_index("uq_news_dedup", "news_item", ["source_key", "content_hash"], unique=True)
    op.create_index("ix_news_item_source_key", "news_item", ["source_key"])
    op.create_index("idx_news_simhash", "news_item", ["simhash"])
    op.create_index("idx_news_publish_desc", "news_item", ["publish_time"])
    op.create_index("idx_news_score_time", "news_item", ["score", "publish_time"])
    op.create_index("idx_news_band_time", "news_item", ["band", "publish_time"])
    op.create_index("idx_news_status_open", "news_item", ["status"])

    # ---------------- news_score ----------------
    op.create_table(
        "news_score",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("news_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column("band", score_band, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("is_suspect", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("batch_id", sa.String(64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["news_id"], ["news_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("score BETWEEN 1 AND 10", name="ck_score_range"),
    )
    op.create_index("idx_score_news", "news_score", ["news_id", "created_at"])

    # ---------------- news_chunk ----------------
    op.create_table(
        "news_chunk",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("news_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("embedding", Vector(VECTOR_DIM), nullable=False),
        sa.Column("score", sa.SmallInteger(), nullable=True),
        sa.Column("band", score_band, nullable=True),
        sa.Column("publish_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entity_codes", postgresql.ARRAY(sa.String(32)), nullable=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["news_id"], ["news_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_chunk", "news_chunk", ["news_id", "chunk_index"], unique=True)
    op.create_index("idx_chunk_news", "news_chunk", ["news_id"])
    op.create_index(
        "idx_chunk_embedding",
        "news_chunk",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("idx_chunk_entities", "news_chunk", ["entity_codes"], postgresql_using="gin")

    # ---------------- news_entity ----------------
    op.create_table(
        "news_entity",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("news_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_type", entity_type, nullable=False),
        sa.Column("code", sa.String(32), nullable=True),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("extra", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["news_id"], ["news_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_entity_news", "news_entity", ["news_id"])
    op.create_index("idx_entity_code", "news_entity", ["code"])

    # ---------------- ingest_event ----------------
    op.create_table(
        "ingest_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", event_status, nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("locked_by", sa.String(64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ingest_event_status", "ingest_event", ["status"])
    op.create_index(
        "idx_event_poll",
        "ingest_event",
        ["priority", "created_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "idx_event_available",
        "ingest_event",
        ["available_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index("idx_event_agg", "ingest_event", ["aggregate_type", "aggregate_id", "event_type"])
    op.create_index(
        "uq_event_pending_dedup",
        "ingest_event",
        ["event_type", "aggregate_id"],
        unique=True,
        # 必须用 OR 形式：ON CONFLICT 的 index_where 才能被 PG 判定为匹配
        # （IN (...) 会被存成 status = ANY(ARRAY[...])，导致推断失败）
        postgresql_where=sa.text("status = 'PENDING' OR status = 'PROCESSING'"),
    )

    # ---------------- agent_run ----------------
    op.create_table(
        "agent_run",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("agent_type", agent_type, nullable=False),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=True),
        sa.Column("status", run_status, nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("input_digest", sa.String(64), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("result_ref", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_cent", sa.Float(), nullable=True),
        sa.Column("error_type", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index(
        "uq_run_idem",
        "agent_run",
        ["agent_type", "subject_id", "prompt_version", "input_digest"],
        unique=True,
    )
    op.create_index("idx_run_status", "agent_run", ["status", "created_at"])
    op.create_index("idx_run_subject", "agent_run", ["subject_type", "subject_id", "created_at"])

    # ---------------- llm_call_log ----------------
    op.create_table(
        "llm_call_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("role", sa.String(32), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("is_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("request_chars", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="OK"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cost_cent", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_llm_created", "llm_call_log", ["created_at"])
    op.create_index("idx_llm_role_day", "llm_call_log", ["role", "created_at"])

    # ---------------- dead_letter / ingest_error / prompt_template ----------------
    op.create_table(
        "dead_letter",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_table", sa.String(48), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(48), nullable=True),
        sa.Column("error_type", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ingest_error",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_key", sa.String(64), nullable=True),
        sa.Column("kind", sa.String(32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "prompt_template",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("agent_type", agent_type, nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("user_template", sa.Text(), nullable=True),
        sa.Column("response_schema", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # ---------------- analysis_report ----------------
    op.create_table(
        "analysis_report",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_type", agent_type, nullable=False),
        sa.Column("news_id", sa.BigInteger(), nullable=True),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("period", market_period, nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("score", sa.SmallInteger(), nullable=True),
        sa.Column("band", score_band, nullable=True),
        sa.Column("sentiment", sa.String(16), nullable=True),
        sa.Column("impact_level", sa.String(16), nullable=True),
        sa.Column("horizon", sa.String(16), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("beneficiaries", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("victims", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("entities", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("references", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("external_sources", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", report_status, nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_cent", sa.Float(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["news_id"], ["news_item.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "uq_report_news_agent",
        "analysis_report",
        ["news_id", "agent_type", "prompt_version"],
        unique=True,
        postgresql_where=sa.text("status IN ('DRAFT','PUBLISHED','DEGRADED')"),
    )
    op.create_index(
        "uq_report_brief",
        "analysis_report",
        ["trade_date", "period", "prompt_version"],
        unique=True,
        postgresql_where=sa.text("period IN ('pre_market','post_market')"),
    )
    op.create_index("idx_report_pub", "analysis_report", ["published_at"])
    op.create_index("idx_report_type_time", "analysis_report", ["agent_type", "published_at"])
    op.create_index("idx_report_band", "analysis_report", ["band", "published_at"])
    op.create_index("idx_report_trade_date", "analysis_report", ["trade_date"])
    op.create_index("idx_report_entities", "analysis_report", ["entities"], postgresql_using="gin")

    # ---------------- market_daily / 行情缓存 ----------------
    op.create_table(
        "market_daily",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("is_trading_day", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("index_bars", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("advance", sa.Integer(), nullable=True),
        sa.Column("decline", sa.Integer(), nullable=True),
        sa.Column("flat", sa.Integer(), nullable=True),
        sa.Column("limit_up", sa.Integer(), nullable=True),
        sa.Column("limit_down", sa.Integer(), nullable=True),
        sa.Column("total_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("sectors_top", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("sectors_bottom", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("us_overnight", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("news_highlights", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("stats_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("trade_date"),
    )
    op.create_table(
        "stock_daily",
        sa.Column("ts_code", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(12, 4), nullable=True),
        sa.Column("high", sa.Numeric(12, 4), nullable=True),
        sa.Column("low", sa.Numeric(12, 4), nullable=True),
        sa.Column("close", sa.Numeric(12, 4), nullable=True),
        sa.Column("pct_chg", sa.Float(), nullable=True),
        sa.Column("vol", sa.Numeric(20, 4), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.PrimaryKeyConstraint("ts_code", "trade_date"),
    )
    op.create_index("idx_stock_daily_date", "stock_daily", ["trade_date"])
    op.create_table(
        "stock_daily_basic",
        sa.Column("ts_code", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("close", sa.Numeric(12, 4), nullable=True),
        sa.Column("turnover_rate", sa.Float(), nullable=True),
        sa.Column("volume_ratio", sa.Float(), nullable=True),
        sa.Column("pe_ttm", sa.Float(), nullable=True),
        sa.Column("pb", sa.Float(), nullable=True),
        sa.Column("ps_ttm", sa.Float(), nullable=True),
        sa.Column("dv_ttm", sa.Float(), nullable=True),
        sa.Column("total_mv", sa.Numeric(20, 4), nullable=True),
        sa.PrimaryKeyConstraint("ts_code", "trade_date"),
    )
    op.create_index("idx_basic_date", "stock_daily_basic", ["trade_date"])
    op.create_table(
        "index_daily_bar",
        sa.Column("ts_code", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(32), nullable=True),
        sa.Column("close", sa.Numeric(12, 4), nullable=True),
        sa.Column("pct_chg", sa.Float(), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.PrimaryKeyConstraint("ts_code", "trade_date"),
    )
    op.create_table(
        "us_daily_bar",
        sa.Column("ts_code", sa.String(32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("close", sa.Numeric(14, 4), nullable=True),
        sa.Column("pct_chg", sa.Float(), nullable=True),
        sa.Column("pe", sa.Float(), nullable=True),
        sa.Column("pb", sa.Float(), nullable=True),
        sa.Column("total_mv", sa.Numeric(20, 4), nullable=True),
        sa.PrimaryKeyConstraint("ts_code", "trade_date"),
    )
    op.create_table(
        "top_list_bar",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(16), nullable=True),
        sa.Column("name", sa.String(32), nullable=True),
        sa.Column("close", sa.Numeric(12, 4), nullable=True),
        sa.Column("pct_chg", sa.Float(), nullable=True),
        sa.Column("net_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_lhb_date", "top_list_bar", ["trade_date"])
    op.create_table(
        "stock_forecast",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts_code", sa.String(16), nullable=True),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("type", sa.String(16), nullable=True),
        sa.Column("p_change_min", sa.Float(), nullable=True),
        sa.Column("p_change_max", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "stock_basic",
        sa.Column("ts_code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(32), nullable=True),
        sa.Column("industry", sa.String(32), nullable=True),
        sa.Column("market", sa.String(16), nullable=True),
        sa.Column("list_date", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("ts_code"),
    )
    op.create_table(
        "sector",
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("type", sa.String(16), nullable=True),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_table(
        "sector_member",
        sa.Column("sector_code", sa.String(32), nullable=False),
        sa.Column("ts_code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("sector_code", "ts_code"),
    )
    op.create_table(
        "trade_calendar",
        sa.Column("exchange", sa.String(8), nullable=False),
        sa.Column("cal_date", sa.Date(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("exchange", "cal_date"),
    )
    op.create_table(
        "ingest_cursor",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("kind", ingest_kind, nullable=False),
        sa.Column("cursor_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("overlap_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(16), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_count", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("extra", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_key"),
    )

    # ---------------- chat ----------------
    op.create_table(
        "chat_session",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("context_filter", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index("idx_chat_device", "chat_session", ["device_id", "last_message_at"])
    op.create_table(
        "chat_message",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("references", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("retrieved_chunk_ids", postgresql.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="OK"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_session.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_msg_session", "chat_message", ["session_id", "id"])


def downgrade() -> None:
    for table in (
        "chat_message", "chat_session", "ingest_cursor", "trade_calendar", "sector_member", "sector",
        "stock_basic", "stock_forecast", "top_list_bar", "us_daily_bar", "index_daily_bar",
        "stock_daily_basic", "stock_daily", "market_daily", "analysis_report", "prompt_template",
        "ingest_error", "dead_letter", "llm_call_log", "agent_run", "ingest_event", "news_entity",
        "news_chunk", "news_score", "news_item",
    ):
        op.drop_table(table)

    for enum_ in (
        market_period, report_status, event_status, run_status, agent_type,
        entity_type, ingest_kind, news_status, score_band,
    ):
        enum_.drop(op.get_bind(), checkfirst=True)
