"""事件队列与运行记录：ingest_event / agent_run / llm_call_log / dead_letter / ingest_error。"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from fin_news.core.enums import AgentType, EventStatus, EventType, RunStatus
from fin_news.models.base import Base, TimestampMixin, pg_enum

run_status_t = pg_enum(RunStatus, "run_status")
event_status_t = pg_enum(EventStatus, "event_status")
agent_type_t = pg_enum(AgentType, "agent_type")


class IngestEvent(Base, TimestampMixin):
    """库内事件队列（Outbox/Inbox 合一），由 pipeline worker 消费。"""

    __tablename__ = "ingest_event"
    __table_args__ = (
        Index("idx_event_poll", "priority", "created_at"),
        Index("idx_event_available", "available_at"),
        Index("idx_event_agg", "aggregate_type", "aggregate_id", "event_type"),
        # 软去重：同一聚合 + 同一事件类型只允许一条未处理事件
        # 谓词用 OR 形式，保证 ON CONFLICT (index_where=...) 能被 PG 匹配
        Index(
            "uq_event_pending_dedup",
            "event_type",
            "aggregate_id",
            unique=True,
            postgresql_where=text("status = 'PENDING' OR status = 'PROCESSING'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    status: Mapped[EventStatus] = mapped_column(
        event_status_t, nullable=False, default=EventStatus.PENDING, index=True
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def event_type_enum(self) -> EventType | None:
        try:
            return EventType(self.event_type)
        except ValueError:
            return None


class AgentRun(Base, TimestampMixin):
    """Agent 运行记录（可观测 + 幂等去重）。"""

    __tablename__ = "agent_run"
    __table_args__ = (
        Index(
            "uq_run_idem",
            "agent_type",
            "subject_id",
            "prompt_version",
            "input_digest",
            unique=True,
        ),
        Index("idx_run_status", "status", "created_at"),
        Index("idx_run_subject", "subject_type", "subject_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(String(64), nullable=False, unique=True)

    agent_type: Mapped[AgentType] = mapped_column(agent_type_t, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(64))

    status: Mapped[RunStatus] = mapped_column(run_status_t, nullable=False, default=RunStatus.PENDING)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    input_digest: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result_ref: Mapped[int | None] = mapped_column(BigInteger)

    latency_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_cent: Mapped[float | None] = mapped_column()

    error_type: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(64))

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LLMCallLog(Base, TimestampMixin):
    """模型调用审计。"""

    __tablename__ = "llm_call_log"
    __table_args__ = (
        Index("idx_llm_created", "created_at"),
        Index("idx_llm_role_day", "role", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(64))

    provider: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    request_chars: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OK")
    error_message: Mapped[str | None] = mapped_column(Text)
    cost_cent: Mapped[float | None] = mapped_column()


class DeadLetter(Base, TimestampMixin):
    """超过最大重试次数的事件 / 任务，供人工重放。"""

    __tablename__ = "dead_letter"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_table: Mapped[str] = mapped_column(String(48), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str | None] = mapped_column(String(48))
    error_type: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestError(Base, TimestampMixin):
    """接入侧单条脏数据 / 单源失败留痕，不阻断主流程。"""

    __tablename__ = "ingest_error"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_key: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
    )


class PromptTemplate(Base, TimestampMixin):
    """Prompt 版本化存储（可选，用于热更新与追溯）。"""

    __tablename__ = "prompt_template"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_type: Mapped[AgentType] = mapped_column(agent_type_t, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_template: Mapped[str | None] = mapped_column(Text)
    response_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
