"""接入时间窗的时区一致性（回归测试）。

背景：``IngestCursor.cursor_time`` 是 timestamptz，asyncpg 读回一律是 UTC（+00:00）；
而 ``timeutil.now()`` 返回 Asia/Shanghai（+08:00）。两者直接混用后若拿去 strftime
发给 Tushare（其 start_date/end_date 按北京时间解释），窗口起点会被提前 8 小时，
导致每轮多拉约 8 小时数据。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fin_news.core.config import Settings
from fin_news.core.enums import IngestKind
from fin_news.core.timeutil import MARKET_TZ
from fin_news.ingestion.service import IngestionService
from fin_news.ingestion.sources.tushare_news import TushareNewsSource, api_dt
from fin_news.models.analysis import IngestCursor

BEIJING_OFFSET = timedelta(hours=8)


def _settings() -> Settings:
    """不读 .env，避免测试依赖本机环境。"""
    return Settings(_env_file=None)


def _bj(year: int, month: int, day: int, hour: int, minute: int, second: int) -> datetime:
    """构造一个北京时间的 aware datetime。"""
    return datetime(year, month, day, hour, minute, second, tzinfo=MARKET_TZ)


def _cursor(cursor_time: datetime, overlap_seconds: int = 300) -> IngestCursor:
    return IngestCursor(
        source_key="tushare.news.cls",
        kind=IngestKind.NEWS,
        cursor_time=cursor_time,
        overlap_seconds=overlap_seconds,
        enabled=True,
    )


class _FakeClient:
    """只记录请求参数，不发真实请求。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def query(self, api_name: str, **kwargs):
        self.calls.append({"api_name": api_name, **kwargs})
        return None


def test_api_dt_formats_as_beijing_wall_clock_regardless_of_tzinfo():
    """同一绝对时刻，无论入参 tzinfo 是什么，输出都应是北京时间字符串。"""
    utc_dt = datetime(2026, 9, 7, 9, 32, 12, tzinfo=UTC)
    sh_dt = utc_dt.astimezone(MARKET_TZ)

    assert api_dt(utc_dt) == "2026-09-07 17:32:12"
    assert api_dt(sh_dt) == "2026-09-07 17:32:12"
    # naive 按北京时间墙钟解释（与 parse_news_datetime 的约定保持一致），不做偏移换算
    assert api_dt(sh_dt.replace(tzinfo=None)) == "2026-09-07 17:32:12"


def test_window_normalizes_utc_cursor_to_market_tz(monkeypatch):
    """修 bug 前 since 是 +00:00、until 是 +08:00；修完两者应统一为 +08:00。"""
    fixed_now = _bj(2026, 9, 7, 17, 38, 12)
    monkeypatch.setattr("fin_news.ingestion.service.now", lambda: fixed_now)

    # 模拟 asyncpg 从 timestamptz 读回的 UTC 值
    cursor = _cursor(datetime(2026, 9, 7, 9, 37, 12, tzinfo=UTC))
    since, until = IngestionService(sources=[], settings=_settings())._window(cursor)

    assert since.utcoffset() == BEIJING_OFFSET
    assert until.utcoffset() == BEIJING_OFFSET
    assert api_dt(since) == "2026-09-07 17:32:12"
    assert api_dt(until) == "2026-09-07 17:38:12"
    # 窗口跨度 6 分钟，与日志里的 window_minutes 一致
    assert (until - since).total_seconds() == 360


async def test_fetch_window_sends_beijing_time_to_tushare():
    """端到端：即便 since 是 UTC，发给 Tushare 的也应是北京时间。"""
    client = _FakeClient()
    source = TushareNewsSource("cls", client=client, settings=_settings())

    # 模拟 _window 修复前的场景：since 为 UTC，until 为北京时间
    since_utc = datetime(2026, 9, 7, 9, 32, 12, tzinfo=UTC)
    until_sh = _bj(2026, 9, 7, 17, 38, 12)
    await source._fetch_window(since_utc, until_sh)

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["api_name"] == "news"
    assert call["src"] == "cls"
    assert call["start_date"] == "2026-09-07 17:32:12"
    assert call["end_date"] == "2026-09-07 17:38:12"
