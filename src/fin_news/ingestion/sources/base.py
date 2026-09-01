"""数据源抽象。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from fin_news.core.enums import IngestKind
from fin_news.domain.schemas import RawItem


@dataclass(frozen=True)
class SourceMeta:
    source_key: str
    src: str
    src_name: str
    kind: IngestKind = IngestKind.NEWS
    api_name: str = "news"
    extra: dict | None = None


class NewsSource(ABC):
    """资讯数据源接口：给定时间窗，返回归一化前的原始条目。"""

    meta: SourceMeta

    def __init__(self, meta: SourceMeta) -> None:
        self.meta = meta

    @property
    def source_key(self) -> str:
        return self.meta.source_key

    @abstractmethod
    async def fetch(self, since: datetime, until: datetime) -> list[RawItem]:
        """拉取 [since, until) 区间的原始数据。"""
