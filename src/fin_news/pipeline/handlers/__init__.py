"""事件处理器集合。"""
from fin_news.pipeline.handlers import on_embedded, on_ingested, on_scored

HANDLERS = {
    "news.ingested": on_ingested.handle,
    "news.scored": on_scored.handle,
    "news.embedded": on_embedded.handle,
}

__all__ = ["HANDLERS", "on_ingested", "on_scored", "on_embedded"]
