"""事件模块：类型定义与库内事件总线。"""
from fin_news.events.bus import EventBus
from fin_news.events.types import EVENT_SPECS, EventSpec

__all__ = ["EventBus", "EVENT_SPECS", "EventSpec"]
