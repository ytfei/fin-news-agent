"""Agent 结构化输出的 Pydantic 模型（替代原先 "prompt 里贴 JSON Schema + 正则兜底"）。

约定：
* 这里只做**结构**约束，不做取值范围约束（分数越界在图的 validate 节点里 clamp，
  而不是让 provider 侧 schema 直接拒绝，避免整批失败）
* 升级模型 = 改这里的字段 + prompt_version，历史报告仍绑定旧版本可复现
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())


# ------------------------------ 评分 ------------------------------


class ScoreEntityModel(_Base):
    type: Literal["stock", "sector", "index", "macro"] = "macro"
    code: str | None = None
    name: str | None = None
    confidence: float = 0.5


class ScoreItemModel(_Base):
    """单条资讯的评分结果。id 为输入列表中的编号（从 1 开始）。"""

    id: int
    score: int = Field(description="1-10 整数，衡量对市场的影响程度")
    reason: str = Field(default="", description="一句话评分依据")
    tags: list[str] = Field(default_factory=list)
    entities: list[ScoreEntityModel] = Field(default_factory=list)
    confidence: float = 0.5


class ScoreBatchModel(_Base):
    """批量评分结果。"""

    items: list[ScoreItemModel] = Field(default_factory=list)
