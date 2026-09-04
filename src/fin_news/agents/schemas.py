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


# ------------------------------ 分析输出 ------------------------------


class EntityItemModel(_Base):
    """受益 / 受损标的（beneficiaries / victims 的元素）。

    方向不放在模型里：beneficiaries 一律 positive、victims 一律 negative，
    由所在列表决定（与落库时 `_extract_entities` 的约定一致）。
    """

    code: str | None = None
    name: str | None = None
    type: Literal["stock", "sector", "index"] = "sector"
    reason: str = ""


class AnalysisPayload(_Base):
    """深度分析报告的通用结构化输出。

    headline / summary 等核心字段都有原生 schema 保证，不再靠正则从文本里捞 JSON；
    extras 保持 dict（各 Agent 特有的自由结构字段，存 JSONB 的 API 契约不变）。
    """

    headline: str = ""
    summary: str = ""
    bullets: list[str] = Field(default_factory=list)
    logic_chain: list[str] = Field(default_factory=list)
    beneficiaries: list[EntityItemModel] = Field(default_factory=list)
    victims: list[EntityItemModel] = Field(default_factory=list)
    watch_list: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0, le=1)
    sentiment: Literal["positive", "negative", "neutral", "mixed"] = "neutral"
    impact_level: Literal["high", "medium", "low"] = "medium"
    horizon: Literal["intraday", "short", "medium", "long"] = "short"
    extras: dict[str, object] = Field(default_factory=dict)


class ArticlePayload(_Base):
    """微信公众号文章的结构化输出。

    content 是完整正文 markdown（活人感风格），title/summary 供列表页展示。
    referenced_article_ids 记录引用的历史文章 public_id（记忆/连续性溯源）。
    """

    title: str = ""
    summary: str = ""
    content: str = Field(default="", description="公众号正文（markdown）")
    tags: list[str] = Field(default_factory=list)
    # 宏观 / 行业 / 个股 等主题段落（可选结构化标注）
    topics: list[dict[str, object]] = Field(default_factory=list)
    referenced_article_ids: list[str] = Field(default_factory=list)
    cover_hint: str | None = Field(default=None, description="封面图建议（供后续图片 skill 使用）")
