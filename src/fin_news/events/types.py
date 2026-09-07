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


# 优先级：快链路（评分 / 向量化）**高于**慢链路（深度分析）。
#
# 原因：pipeline worker 是单循环消费（见 pipeline/worker.py），深度分析单批耗时可达
# 数十分钟。若三类事件同优先级，poll 出的批次会被慢的分析事件占满，新同步的资讯
# 迟迟拿不到评分——表现为「agent 一直在跑、评分却不动」。
# 让快事件排在前面，可保证评分/向量化始终优先推进。
#
# 手动触发的分析事件优先级为 settings.manual_priority（默认 10），仍高于此处全部值。
EVENT_SPECS: dict[EventType, EventSpec] = {
    EventType.NEWS_INGESTED: EventSpec(EventType.NEWS_INGESTED, "news_item", priority=5),
    EventType.NEWS_SCORED: EventSpec(EventType.NEWS_SCORED, "news_item", priority=5),
    EventType.NEWS_EMBEDDED: EventSpec(EventType.NEWS_EMBEDDED, "news_item", priority=2),
    EventType.ANALYSIS_PUBLISHED: EventSpec(EventType.ANALYSIS_PUBLISHED, "analysis_report", priority=1),
}


def news_ingested_payload(score: int | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if score is not None:
        payload["score"] = score
    payload.update(extra)
    return payload
