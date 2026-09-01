"""内部 DTO（Pydantic）：模块间传递的数据结构。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fin_news.core.enums import IngestKind, ScoreBand


class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())


# ------------------------------ 接入侧 ------------------------------


class RawItem(_Base):
    """数据源返回的原始条目（归一化前）。"""

    external_id: str | None = None
    title: str | None = None
    content: str | None = None
    publish_time: datetime
    channels: str | None = None
    url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class NormalizedItem(_Base):
    """归一化并去重后的条目，准备写入 news_item。"""

    source: str = "tushare"
    source_key: str
    kind: IngestKind = IngestKind.NEWS
    src: str | None = None
    src_name: str | None = None
    external_id: str | None = None

    title: str
    content: str
    content_truncated: bool = False
    channels: str | None = None
    url: str | None = None
    publish_time: datetime

    content_hash: str
    simhash: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResult(_Base):
    source_key: str
    fetched: int = 0
    inserted: int = 0
    duplicates: int = 0
    filtered: int = 0
    errors: int = 0
    cursor_time: datetime | None = None
    status: Literal["OK", "PARTIAL", "FAILED", "SKIPPED"] = "OK"
    message: str | None = None


# ------------------------------ 评分侧 ------------------------------


class ScoreEntity(_Base):
    type: Literal["stock", "sector", "index", "macro"] = "macro"
    code: str | None = None
    name: str | None = None
    confidence: float = 0.5


class ScoreItemResult(_Base):
    id: int
    score: int
    reason: str = ""
    tags: list[str] = Field(default_factory=list)
    entities: list[ScoreEntity] = Field(default_factory=list)
    confidence: float = 0.5


class ScoreBatchResult(_Base):
    items: list[ScoreItemResult] = Field(default_factory=list)
    model: str = ""
    prompt_version: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # 批内 80% 以上同分时置 True：模型可能"偷懒"，需抽样人工复核
    is_suspect: bool = False


# ------------------------------ 分析侧 ------------------------------


class ImpactTarget(BaseModel):
    code: str | None = None
    name: str | None = None
    type: Literal["stock", "sector", "index"] = "sector"
    reason: str = ""
    direction: Literal["positive", "negative"] = "positive"


class AnalysisPayload(_Base):
    """分析 Agent 的结构化输出（各 Agent 共用的外层）。"""

    headline: str = ""
    summary: str = ""
    bullets: list[str] = Field(default_factory=list)
    logic_chain: list[str] = Field(default_factory=list)
    beneficiaries: list[ImpactTarget] = Field(default_factory=list)
    victims: list[ImpactTarget] = Field(default_factory=list)
    watch_list: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: float = 0.6
    sentiment: Literal["positive", "negative", "neutral", "mixed"] = "neutral"
    impact_level: Literal["high", "medium", "low"] = "medium"
    horizon: Literal["intraday", "short", "medium", "long"] = "short"
    extras: dict[str, Any] = Field(default_factory=dict)
    disclaimer: str = "AI 生成，仅供参考，不构成投资建议。"


class PreMarketPayload(AnalysisPayload):
    us_market: list[dict[str, Any]] = Field(default_factory=list)
    focus_directions: list[dict[str, Any]] = Field(default_factory=list)


class PostMarketPayload(AnalysisPayload):
    verdict: dict[str, Any] = Field(default_factory=dict)
    attribution: list[dict[str, Any]] = Field(default_factory=list)
    next_day_focus: list[str] = Field(default_factory=list)


# ------------------------------ 检索侧 ------------------------------


class SearchHit(_Base):
    news_id: int
    chunk_id: int
    title: str
    snippet: str
    publish_time: datetime | None = None
    score: int | None = None
    band: ScoreBand | None = None
    similarity: float = 0.0
