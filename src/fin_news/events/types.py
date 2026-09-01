"""事件类型定义与构造辅助。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fin_news.core.enums import EventType


@dataclass(frozen=True)
class EventSpec:
    event_type: EventType
    aggregate_type: str
    priority: int = 1


EVENT_SPECS: dict[EventType, EventSpec] = {
    EventType.NEWS_INGESTED: EventSpec(EventType.NEWS_INGESTED, "news_item", priority=2),
    EventType.NEWS_SCORED: EventSpec(EventType.NEWS_SCORED, "news_item", priority=2),
    EventType.NEWS_EMBEDDED: EventSpec(EventType.NEWS_EMBEDDED, "news_item", priority=2),
    EventType.ANALYSIS_PUBLISHED: EventSpec(EventType.ANALYSIS_PUBLISHED, "analysis_report", priority=1),
}


def news_ingested_payload(score: int | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if score is not None:
        payload["score"] = score
    payload.update(extra)
    return payload
