"""评估集（P6）：用于人工抽查评分分档、量化模型与人工的一致率。

设计要点：
* 评估集 `score_eval_set` 是一次人工评估批次，记录抽样条件与最终统计结果
* 样本 `score_eval_label` 在抽样时**快照**模型评分（model_score/model_band），
  避免后续重算评分污染本次评估结果
* 一致率口径（对齐 PRD §8 MVP 验收「分档一致率 ≥ 80%」）：
  - exact_rate：人工分与模型分完全相等的比例
  - band_agree_rate：人工分档与模型分档相同的比例（**验收口径**）
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from fin_news.core.enums import ScoreBand
from fin_news.models.base import Base, PublicIdMixin, TimestampMixin, pg_enum

score_band_t = pg_enum(ScoreBand, "score_band")

# 评估集状态
EVAL_STATUSES = ("DRAFT", "IN_PROGRESS", "DONE")


class ScoreEvalSet(Base, PublicIdMixin, TimestampMixin):
    """评估集：一次人工评估批次。"""

    __tablename__ = "score_eval_set"
    __table_args__ = (Index("idx_evalset_created", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")

    # 抽样配置
    strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="stratified_band")
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    filters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # 进度
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    labeled_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 统计结果（提交标注时增量重算并缓存，供列表页直接展示）
    exact_rate: Mapped[float | None] = mapped_column(Float)
    band_agree_rate: Mapped[float | None] = mapped_column(Float)
    mean_abs_diff: Mapped[float | None] = mapped_column(Float)
    confusion: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    band_stats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScoreEvalLabel(Base, TimestampMixin):
    """评估集样本：模型评分快照 + 人工标注。"""

    __tablename__ = "score_eval_label"
    __table_args__ = (
        Index("uq_evalset_news", "eval_set_id", "news_id", unique=True),
        Index("idx_evallabel_set", "eval_set_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    eval_set_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("score_eval_set.id", ondelete="CASCADE"),
        nullable=False,
    )
    news_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # ---- 模型评分快照（抽样时固定）----
    model_score: Mapped[int | None] = mapped_column(Integer)
    model_band: Mapped[str | None] = mapped_column(score_band_t)
    model_reason: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(String(32))

    # ---- 人工标注 ----
    human_score: Mapped[int | None] = mapped_column(Integer)
    human_band: Mapped[str | None] = mapped_column(score_band_t)
    human_note: Mapped[str | None] = mapped_column(Text)
    labeled_by: Mapped[str | None] = mapped_column(String(64))
    labeled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_agree: Mapped[bool | None] = mapped_column(Boolean)

    @property
    def is_labeled(self) -> bool:
        return self.human_score is not None
