"""全局枚举：与数据库 enum 类型一一对应。"""
from __future__ import annotations

from enum import StrEnum


class ScoreBand(StrEnum):
    """评分区间（左开右闭）。

    (0,3]=NOISE  (3,5]=STOCK  (5,7]=INDUSTRY  (7,10]=MACRO
    """

    NOISE = "NOISE"
    STOCK = "STOCK"
    INDUSTRY = "INDUSTRY"
    MACRO = "MACRO"


class NewsStatus(StrEnum):
    NEW = "NEW"
    SCORING = "SCORING"
    SCORED = "SCORED"
    ARCHIVED_NOISE = "ARCHIVED_NOISE"
    EMBEDDING = "EMBEDDING"
    EMBEDDED = "EMBEDDED"
    ANALYZING = "ANALYZING"
    ANALYZED = "ANALYZED"
    SCORE_FAILED = "SCORE_FAILED"
    EMBED_FAILED = "EMBED_FAILED"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    DEAD = "DEAD"

    @property
    def terminal(self) -> bool:
        return self in (NewsStatus.ANALYZED, NewsStatus.ARCHIVED_NOISE, NewsStatus.DEAD)


class AgentType(StrEnum):
    SCORING = "scoring"
    MACRO_POLICY = "macro_policy"
    INDUSTRY = "industry"
    STOCK = "stock"
    PRE_MARKET = "pre_market"
    POST_MARKET = "post_market"
    QA = "qa"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    DEAD = "DEAD"


class EventStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


class ReportStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    DEGRADED = "DEGRADED"
    SUPERSEDED = "SUPERSEDED"


class EventType(StrEnum):
    NEWS_INGESTED = "news.ingested"
    NEWS_SCORED = "news.scored"
    NEWS_EMBEDDED = "news.embedded"
    ANALYSIS_PUBLISHED = "analysis.published"


class IngestKind(StrEnum):
    NEWS = "news"
    MAJOR_NEWS = "major_news"
    ANNS = "anns"
    FORECAST = "forecast"
    TOP_LIST = "top_list"
    MARKET = "market"


class EntityType(StrEnum):
    STOCK = "stock"
    SECTOR = "sector"
    INDEX = "index"
    MACRO = "macro"


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class ImpactLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Horizon(StrEnum):
    INTRADAY = "intraday"
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class MarketPeriod(StrEnum):
    PRE_MARKET = "pre_market"
    POST_MARKET = "post_market"
