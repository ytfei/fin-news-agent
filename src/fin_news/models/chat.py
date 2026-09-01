"""追问会话模型：chat_session / chat_message。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from fin_news.models.base import Base, PublicIdMixin, TimestampMixin, fk


class ChatSession(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "chat_session"
    __table_args__ = (Index("idx_chat_device", "device_id", "last_message_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str | None] = mapped_column(String(255))
    context_filter: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_message"
    __table_args__ = (Index("idx_msg_session", "session_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, fk("chat_session.id"), nullable=False)

    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    references: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    retrieved_chunk_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    tool_calls: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)

    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OK")
