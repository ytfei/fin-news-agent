"""行情取数工具：给分析 Agent 提供估值与走势数据。"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.core.logging import get_logger
from fin_news.models.analysis import (
    IndexDailyBar,
    MarketDaily,
    StockBasic,
    StockDaily,
    StockDailyBasic,
    TopListBar,
    TradeCalendar,
    USDailyBar,
)
from fin_news.models.news import NewsItem

logger = get_logger("agents.tools.market_data")

US_KEY_INDICES = {
    ".DJI": "道琼斯",
    ".IXIC": "纳斯达克",
    ".INX": "标普500",
    "SOX": "费城半导体",
}


async def is_trading_day(session: AsyncSession, day: date) -> bool:
    """优先查交易日历，未同步时按工作日兜底。"""
    row = await session.execute(
        select(TradeCalendar.is_open).where(
            TradeCalendar.exchange == "SSE", TradeCalendar.cal_date == day
        )
    )
    value = row.scalar_one_or_none()
    if value is None:
        return day.weekday() < 5
    return bool(value)


async def latest_trade_date(session: AsyncSession, before: date | None = None) -> date | None:
    stmt = select(MarketDaily.trade_date).order_by(desc(MarketDaily.trade_date)).limit(1)
    if before:
        stmt = stmt.where(MarketDaily.trade_date <= before)
    return (await session.execute(stmt)).scalar_one_or_none()


async def market_snapshot(session: AsyncSession, trade_date: date) -> dict:
    """当日市场快照（指数、涨跌家数、成交额、板块、隔夜美股）。"""
    row = await session.execute(
        select(MarketDaily).where(MarketDaily.trade_date == trade_date)
    )
    daily = row.scalar_one_or_none()
    if daily is None:
        return {"trade_date": trade_date.isoformat(), "ready": False, "note": "当日行情尚未同步"}

    return {
        "trade_date": daily.trade_date.isoformat(),
        "ready": bool(daily.stats_ready),
        "index_bars": daily.index_bars or [],
        "breadth": {
            "advance": daily.advance,
            "decline": daily.decline,
            "flat": daily.flat,
            "limit_up": daily.limit_up,
            "limit_down": daily.limit_down,
            "total_amount": float(daily.total_amount or 0),
        },
        "sectors_top": daily.sectors_top or [],
        "sectors_bottom": daily.sectors_bottom or [],
        "us_overnight": daily.us_overnight or [],
    }


async def index_history(session: AsyncSession, ts_code: str, days: int = 20) -> list[dict]:
    end = date.today()
    rows = await session.execute(
        select(IndexDailyBar)
        .where(IndexDailyBar.ts_code == ts_code, IndexDailyBar.trade_date >= end - timedelta(days=days * 2))
        .order_by(IndexDailyBar.trade_date.desc())
        .limit(days)
    )
    return [
        {
            "trade_date": r.trade_date.isoformat(),
            "close": float(r.close or 0),
            "pct_chg": float(r.pct_chg or 0),
        }
        for r in rows.scalars().all()
    ]


async def us_overnight(session: AsyncSession, trade_date: date) -> list[dict]:
    """隔夜美股表现（按最近一个美股交易日）。"""
    rows = await session.execute(
        select(USDailyBar)
        .where(USDailyBar.trade_date <= trade_date, USDailyBar.ts_code.in_(list(US_KEY_INDICES)))
        .order_by(desc(USDailyBar.trade_date))
        .limit(20)
    )
    items = rows.scalars().all()
    if not items:
        return []
    latest = items[0].trade_date
    return [
        {
            "symbol": r.ts_code,
            "name": US_KEY_INDICES.get(r.ts_code, r.name or r.ts_code),
            "trade_date": r.trade_date.isoformat(),
            "close": float(r.close or 0),
            "pct_chg": float(r.pct_chg or 0),
        }
        for r in items
        if r.trade_date == latest
    ]


async def stock_snapshot(session: AsyncSession, ts_code: str, days: int = 60) -> dict:
    """个股估值 + 近期走势。"""
    basic = (
        await session.execute(
            select(StockBasic).where(StockBasic.ts_code == ts_code)
        )
    ).scalar_one_or_none()

    valuation_row = (
        await session.execute(
            select(StockDailyBasic)
            .where(StockDailyBasic.ts_code == ts_code)
            .order_by(desc(StockDailyBasic.trade_date))
            .limit(1)
        )
    ).scalar_one_or_none()

    bars = (
        await session.execute(
            select(StockDaily)
            .where(StockDaily.ts_code == ts_code)
            .order_by(desc(StockDaily.trade_date))
            .limit(days)
        )
    ).scalars().all()

    closes = [float(b.close or 0) for b in bars][::-1]
    ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    ret5 = ((closes[-1] / closes[-6]) - 1) * 100 if len(closes) >= 6 and closes[-6] else None

    return {
        "ts_code": ts_code,
        "name": basic.name if basic else None,
        "industry": basic.industry if basic else None,
        "valuation": (
            {
                "trade_date": valuation_row.trade_date.isoformat(),
                "close": float(valuation_row.close or 0),
                "pe_ttm": valuation_row.pe_ttm,
                "pb": valuation_row.pb,
                "ps_ttm": valuation_row.ps_ttm,
                "total_mv": float(valuation_row.total_mv or 0),
                "turnover_rate": valuation_row.turnover_rate,
            }
            if valuation_row
            else None
        ),
        "trend": {
            "ma5": round(ma5, 3) if ma5 else None,
            "ma20": round(ma20, 3) if ma20 else None,
            "ret_5d_pct": round(ret5, 2) if ret5 else None,
            "bars": [
                {"date": b.trade_date.isoformat(), "close": float(b.close or 0), "pct_chg": float(b.pct_chg or 0)}
                for b in bars[:20]
            ],
        },
    }


async def top_list_of_day(session: AsyncSession, trade_date: date, limit: int = 20) -> list[dict]:
    rows = await session.execute(
        select(TopListBar)
        .where(TopListBar.trade_date == trade_date)
        .order_by(desc(TopListBar.net_amount))
        .limit(limit)
    )
    return [
        {
            "ts_code": r.ts_code,
            "name": r.name,
            "pct_chg": float(r.pct_chg or 0),
            "net_amount": float(r.net_amount or 0),
            "reason": r.reason,
        }
        for r in rows.scalars().all()
    ]


async def high_score_news(
    session: AsyncSession,
    start,
    end,
    min_score: int = 5,
    limit: int = 20,
) -> list[dict]:
    """时间窗内的高评分资讯（盘前/盘后简报的输入）。"""
    rows = await session.execute(
        select(NewsItem)
        .where(
            NewsItem.publish_time >= start,
            NewsItem.publish_time <= end,
            NewsItem.score >= min_score,
        )
        .order_by(desc(NewsItem.score), desc(NewsItem.publish_time))
        .limit(limit)
    )
    return [
        {
            "id": n.id,
            "public_id": str(n.public_id),
            "title": n.title,
            "score": n.score,
            "band": n.band.value if n.band else None,
            "publish_time": n.publish_time.isoformat() if n.publish_time else None,
            "score_reason": n.score_reason,
            "content": (n.content or "")[:600],
        }
        for n in rows.scalars().all()
    ]
