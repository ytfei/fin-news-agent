"""数据源实现集合。"""
from fin_news.ingestion.sources.base import NewsSource, SourceMeta
from fin_news.ingestion.sources.tushare_news import (
    SRC_NAMES,
    TushareNewsSource,
    build_news_sources,
)

__all__ = ["NewsSource", "SourceMeta", "TushareNewsSource", "build_news_sources", "SRC_NAMES"]
