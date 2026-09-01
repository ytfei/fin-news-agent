"""Tushare news 接口数据源（财联社 / 华尔街见闻 等快讯）。

接口：`pro.news(src=..., start_date=..., end_date=...)`
- 需要单独开通资讯权限（与积分无关）
- 单次最多 1500 条，超出需按时间窗切分
- 返回字段：datetime / title / content（title 常为 None，由 normalizer 兜底）
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import IngestKind
from fin_news.core.logging import get_logger
from fin_news.core.timeutil import parse_news_datetime
from fin_news.domain.schemas import RawItem
from fin_news.ingestion.sources.base import NewsSource, SourceMeta
from fin_news.ingestion.tushare_client import TushareClient, get_tushare_client

logger = get_logger("ingestion.source.tushare_news")

SINGLE_LIMIT = 1500

# src 标识 -> 中文名
SRC_NAMES = {
    "cls": "财联社",
    "wallstreetcn": "华尔街见闻",
    "sina": "新浪财经",
    "10jqka": "同花顺",
    "eastmoney": "东方财富",
    "yuncaijing": "云财经",
    "fenghuang": "凤凰新闻",
    "jinrongjie": "金融界",
    "yicai": "第一财经",
}


class TushareNewsSource(NewsSource):
    def __init__(
        self,
        src: str,
        client: TushareClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or get_tushare_client(self.settings)
        super().__init__(
            SourceMeta(
                source_key=f"tushare.news.{src}",
                src=src,
                src_name=SRC_NAMES.get(src, src),
                kind=IngestKind.NEWS,
                api_name="news",
            )
        )

    async def fetch(self, since: datetime, until: datetime) -> list[RawItem]:
        windows = self._split_windows(since, until)
        items: list[RawItem] = []
        seen: set[str] = set()

        for start, end in windows:
            try:
                records = await self._fetch_window(start, end)
            except Exception as exc:  # noqa: BLE001 - 记录失败窗口后继续抛出，位点不前进
                logger.error(
                    "拉取窗口失败",
                    src=self.meta.src,
                    start=start.strftime("%Y-%m-%d %H:%M:%S"),
                    end=end.strftime("%Y-%m-%d %H:%M:%S"),
                    error=str(exc)[:300],
                )
                raise
            for rec in records:
                publish_time = parse_news_datetime(rec.get("datetime"))
                if publish_time is None:
                    continue
                # 数据源可能返回窗口外的数据（时区/延迟），按内容去重
                key = f"{publish_time.isoformat()}|{(rec.get('content') or '')[:120]}"
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    RawItem(
                        external_id=None,
                        title=rec.get("title") or None,
                        content=rec.get("content") or "",
                        publish_time=publish_time,
                        channels=rec.get("channels"),
                        url=rec.get("url"),
                        raw=rec,
                    )
                )

        logger.info(
            "拉取资讯",
            src=self.meta.src,
            windows=len(windows),
            items=len(items),
            since=since.strftime("%Y-%m-%d %H:%M:%S"),
            until=until.strftime("%Y-%m-%d %H:%M:%S"),
        )
        return items

    async def _fetch_window(self, start: datetime, end: datetime) -> list[dict]:
        kwargs = {
            "src": self.meta.src,
            "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
        }
        started = time.perf_counter()
        df = await self.client.query(self.meta.api_name, **kwargs)
        records = TushareClient.to_records(df)
        logger.debug(
            "拉取窗口完成",
            src=self.meta.src,
            start=start.strftime("%Y-%m-%d %H:%M:%S"),
            end=end.strftime("%Y-%m-%d %H:%M:%S"),
            records=len(records),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
        return records

    def _split_windows(self, since: datetime, until: datetime) -> list[tuple[datetime, datetime]]:
        """按 6 小时切片，降低单次请求压力并规避单次限量。"""
        step = timedelta(hours=6)
        windows: list[tuple[datetime, datetime]] = []
        cursor = since
        while cursor < until:
            nxt = min(cursor + step, until)
            windows.append((cursor, nxt))
            cursor = nxt
        return windows or [(since, until)]


def build_news_sources(
    srcs: list[str] | None = None,
    client: TushareClient | None = None,
    settings: Settings | None = None,
) -> list[NewsSource]:
    settings = settings or get_settings()
    return [TushareNewsSource(src, client=client, settings=settings) for src in (srcs or settings.news_sources)]
