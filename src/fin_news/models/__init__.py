"""SQLAlchemy 模型汇总（Alembic autogenerate 依赖 Base.metadata）。"""
from fin_news.models.analysis import (
    AnalysisReport,
    IndexDailyBar,
    IngestCursor,
    MarketDaily,
    Sector,
    SectorMember,
    StockBasic,
    StockDaily,
    StockDailyBasic,
    StockForecast,
    TopListBar,
    TradeCalendar,
    USDailyBar,
)
from fin_news.models.base import Base
from fin_news.models.chat import ChatMessage, ChatSession
from fin_news.models.evaluation import ScoreEvalLabel, ScoreEvalSet
from fin_news.models.event import (
    AgentRun,
    DeadLetter,
    IngestError,
    IngestEvent,
    LLMCallLog,
    PromptTemplate,
)
from fin_news.models.news import NewsChunk, NewsEntity, NewsItem, NewsScore
from fin_news.models.wechat import WechatArticle, WechatArticleChunk

__all__ = [
    "Base",
    "NewsItem",
    "NewsScore",
    "NewsChunk",
    "NewsEntity",
    "WechatArticle",
    "WechatArticleChunk",
    "IngestEvent",
    "AgentRun",
    "LLMCallLog",
    "DeadLetter",
    "IngestError",
    "PromptTemplate",
    "IngestCursor",
    "AnalysisReport",
    "MarketDaily",
    "StockDaily",
    "StockDailyBasic",
    "IndexDailyBar",
    "USDailyBar",
    "TopListBar",
    "StockForecast",
    "StockBasic",
    "Sector",
    "SectorMember",
    "TradeCalendar",
    "ChatSession",
    "ChatMessage",
    "ScoreEvalSet",
    "ScoreEvalLabel",
]
