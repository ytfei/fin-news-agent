"""SQLAlchemy 声明式基类与通用 Mixin。"""
from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def pg_enum(enum_cls: type[enum.Enum], name: str) -> PGEnum:
    """PG 枚举类型。

    SQLAlchemy 默认把枚举的 **name** 写入数据库，这里显式改成 **value**，
    保证库里的取值与 API / 文档一致（如 ingest_kind='news' 而非 'NEWS'）。
    """
    return PGEnum(
        enum_cls,
        name=name,
        create_type=False,  # 类型由 Alembic 迁移统一创建
        values_callable=lambda e: [item.value for item in e],
    )


class Base(DeclarativeBase):
    """所有模型共享的元数据（Alembic autogenerate 使用）。"""

    repr_cols_num = 4

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        cols = [
            f"{c.name}={getattr(self, c.name)!r}"
            for c in self.__table__.columns  # type: ignore[attr-defined]
        ][: self.repr_cols_num]
        return f"<{self.__class__.__name__} {', '.join(cols)}>"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PublicIdMixin:
    """对外暴露的 UUID（不暴露自增主键）。"""

    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        unique=True,  # 唯一约束本身即创建索引，无需再建普通索引
        nullable=False,
    )


def fk(table: str, ondelete: str = "CASCADE") -> ForeignKey:
    return ForeignKey(table, ondelete=ondelete)
