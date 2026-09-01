"""时间与时区工具：统一以 Asia/Shanghai 作为业务时区。"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("Asia/Shanghai")
UTC = UTC


def now() -> datetime:
    """当前时间（含时区，Asia/Shanghai）。"""
    return datetime.now(tz=UTC).astimezone(MARKET_TZ)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def to_market_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MARKET_TZ)
    return dt.astimezone(MARKET_TZ)


def parse_news_datetime(value: str | datetime | None) -> datetime | None:
    """解析 Tushare 返回的 'YYYY-MM-DD HH:MM:SS'（无时区，按北京时间理解）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_market_tz(value)
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=MARKET_TZ)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).replace(tzinfo=MARKET_TZ)
    except ValueError:
        return None


def market_today() -> date:
    return now().date()


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def previous_days(d: date, n: int) -> date:
    return d - timedelta(days=n)


def format_dt(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str | None:
    return dt.strftime(fmt) if dt else None
