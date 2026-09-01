"""API 响应模型（与 docs/openapi.yaml 对齐）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, protected_namespaces=())


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    has_more: bool


class Page(_Base, Generic[T]):
    page: int = 1
    page_size: int = 20
    total: int = 0
    has_more: bool = False
    items: list[T] = Field(default_factory=list)


class EntityOut(_Base):
    type: str = "macro"
    code: str | None = None
    name: str | None = None
    confidence: float | None = None


class ImpactTargetOut(_Base):
    code: str | None = None
    name: str | None = None
    type: str = "sector"
    reason: str = ""
    direction: str = "positive"


class NewsItemOut(_Base):
    id: str
    title: str
    summary: str | None = None
    source: str
    src: str | None = None
    src_name: str | None = None
    kind: str | None = None
    channels: str | None = None
    publish_time: datetime
    ingested_at: datetime | None = None
    score: int | None = None
    band: str | None = None
    score_reason: str | None = None
    tags: list[str] = Field(default_factory=list)
    entities: list[EntityOut] = Field(default_factory=list)
    has_analysis: bool = False
    analysis_summary: str | None = None
    analysis_id: str | None = None
    seen_count: int = 1


class RelatedNewsOut(_Base):
    id: str
    title: str
    publish_time: datetime | None = None
    score: int | None = None
    similarity: float = 0.0


class ScoreHistoryOut(_Base):
    score: int
    band: str | None = None
    reason: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    created_at: datetime | None = None


class NewsDetailOut(NewsItemOut):
    content: str | None = None
    content_truncated: bool = False
    url: str | None = None
    score_history: list[ScoreHistoryOut] = Field(default_factory=list)
    related_news: list[RelatedNewsOut] = Field(default_factory=list)


class AnalysisReportOut(_Base):
    id: str
    agent_type: str
    news_id: str | None = None
    news_title: str | None = None
    trade_date: date | None = None
    title: str
    summary: str
    score: int | None = None
    band: str | None = None
    sentiment: str | None = None
    impact_level: str | None = None
    horizon: str | None = None
    confidence: float | None = None
    beneficiaries: list[ImpactTargetOut] = Field(default_factory=list)
    victims: list[ImpactTargetOut] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    references: list[Any] = Field(default_factory=list)
    status: str
    model: str | None = None
    prompt_version: str | None = None
    published_at: datetime | None = None
    disclaimer: str = "AI 生成，仅供参考，不构成投资建议。"


class AnalysisDetailOut(AnalysisReportOut):
    content: dict[str, Any] = Field(default_factory=dict)
    external_sources: list[dict[str, Any]] = Field(default_factory=list)
    run: dict[str, Any] | None = None


class IndexQuoteOut(_Base):
    code: str
    name: str
    close: float | None = None
    pct_chg: float | None = None
    amount: float | None = None


class BreadthOut(_Base):
    advance: int | None = None
    decline: int | None = None
    flat: int | None = None
    limit_up: int | None = None
    limit_down: int | None = None
    total_amount: float | None = None


class SectorQuoteOut(_Base):
    code: str
    name: str | None = None
    pct_chg: float | None = None
    turnover: float | None = None
    leading_stock: dict[str, Any] | None = None


class MarketOverviewOut(_Base):
    trade_date: date
    is_trading_day: bool = True
    updated_at: datetime | None = None
    indices: list[IndexQuoteOut] = Field(default_factory=list)
    breadth: BreadthOut | None = None
    sectors_top: list[SectorQuoteOut] = Field(default_factory=list)
    sectors_bottom: list[SectorQuoteOut] = Field(default_factory=list)
    headline: str | None = None


class UsQuoteOut(_Base):
    symbol: str
    name: str | None = None
    close: float | None = None
    pct_chg: float | None = None
    trade_date: date | None = None


class BriefMetaOut(_Base):
    trade_date: date
    period: str
    report_id: str
    title: str
    summary: str = ""
    published_at: datetime | None = None


class PreMarketBriefOut(AnalysisDetailOut):
    us_market: list[UsQuoteOut] = Field(default_factory=list)
    focus_directions: list[dict[str, Any]] = Field(default_factory=list)


class PostMarketBriefOut(AnalysisDetailOut):
    verdict: dict[str, Any] = Field(default_factory=dict)
    attribution: list[dict[str, Any]] = Field(default_factory=list)
    next_day_focus: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=20, ge=1, le=100)
    start: datetime | None = None
    end: datetime | None = None
    band: list[str] | None = None
    min_score: int | None = None
    codes: list[str] | None = None
    mode: Literal["hybrid", "vector", "keyword"] = "hybrid"


class SearchHitOut(_Base):
    news_id: str
    chunk_id: int
    title: str
    snippet: str
    publish_time: datetime | None = None
    score: int | None = None
    band: str | None = None
    similarity: float = 0.0
    analysis_id: str | None = None


class StockProfileOut(_Base):
    ts_code: str
    name: str | None = None
    industry: str | None = None
    market: str | None = None
    latest: dict[str, Any] | None = None
    trend: list[dict[str, Any]] = Field(default_factory=list)


class ChatSessionOut(_Base):
    id: str
    title: str | None = None
    context_filter: dict[str, Any] = Field(default_factory=dict)
    message_count: int = 0
    created_at: datetime | None = None
    last_message_at: datetime | None = None


class ChatMessageOut(_Base):
    id: str
    session_id: str
    role: str
    content: str
    references: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "OK"
    model: str | None = None
    latency_ms: int | None = None
    created_at: datetime | None = None
    disclaimer: str | None = None


class HealthOut(BaseModel):
    status: str = "ok"
    db: str = "up"
    llm: str = "unknown"
    event_backlog: int = 0
    version: str = "0.1.0"
    time: datetime


class BacklogOut(BaseModel):
    pending: int = 0
    overdue: int = 0
    dead_letter: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)


class Problem(BaseModel):
    type: str = "about:blank"
    title: str = "错误"
    status: int = 500
    detail: str | None = None
    instance: str | None = None
    trace_id: str | None = None
