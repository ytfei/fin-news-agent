"""行情 / 基本面数据同步（Tushare）。

当前账号实测权限（2026-09-01 校准）：

| 接口 | 权限 | 落表 |
| --- | --- | --- |
| `trade_cal` | ✅ | `trade_calendar` |
| `stock_basic` | ✅ | `stock_basic` |
| `daily` | ✅ 全市场 ~5500 行/交易日 | `stock_daily` |
| `daily_basic` | ✅ | `stock_daily_basic` |
| `index_daily` | ✅ | `index_daily_bar` |
| `us_daily` | ❌ **无权限** | `us_daily_bar`（留空） |

因此盘前简报的「隔夜美股」不依赖 `us_daily`，而是从近期资讯中提取美股相关
标题（`build_us_overnight_from_news`），保证无权限也能生成简报，只是来源降级。

同步采用 upsert（ON CONFLICT DO UPDATE），可重复执行。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.core.logging import get_logger
from fin_news.ingestion.tushare_client import TushareClient, get_tushare_client
from fin_news.models.analysis import (
    IndexDailyBar,
    MarketDaily,
    StockBasic,
    StockDaily,
    StockDailyBasic,
    TradeCalendar,
)

logger = get_logger("ingestion.market")

# 关注的宽基指数（用于 market_daily.index_bars 与大盘状态判断）
INDEX_CODES: dict[str, str] = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
}

# 涨跌停近似阈值（A 股主板 10%、创业板/科创板 20%，这里用保守下限识别）
LIMIT_UP_PCT = 9.8
LIMIT_DOWN_PCT = -9.8

_UPSERT_BATCH = 1000


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def _parse_date(value: Any) -> date | None:
    """Tushare 日期字段统一为 date（支持 20260901 / 2026-09-01 / date）。"""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) >= 8 and text[:8].isdigit():
        return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _f(value: Any) -> float | None:
    """转 float，空值/NaN 统一为 None。"""
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result  # 过滤 NaN


async def _upsert(
    session: AsyncSession,
    model: type,
    rows: list[dict[str, Any]],
    index_elements: list[str],
) -> int:
    """批量 upsert，返回行数。"""
    if not rows:
        return 0
    columns = [c for c in rows[0] if c not in index_elements]
    for start in range(0, len(rows), _UPSERT_BATCH):
        chunk = rows[start : start + _UPSERT_BATCH]
        stmt = pg_insert(model).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=index_elements,
            set_={c: getattr(stmt.excluded, c) for c in columns},
        )
        await session.execute(stmt)
    await session.flush()
    return len(rows)


# ----------------------------------------------------------------------
# 各接口同步
# ----------------------------------------------------------------------
async def sync_trade_calendar(
    session: AsyncSession,
    client: TushareClient | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    exchange: str = "SSE",
) -> int:
    """同步交易日历（默认覆盖去年到明年，保证盘前/盘后能判断交易日）。"""
    client = client or get_tushare_client()
    today = date.today()
    start = start_date or (today - timedelta(days=365)).strftime("%Y%m%d")
    end = end_date or (today + timedelta(days=365)).strftime("%Y%m%d")

    df = await client.query("trade_cal", exchange=exchange, start_date=start, end_date=end)
    records = TushareClient.to_records(df)

    rows: list[dict[str, Any]] = []
    for rec in records:
        cal_date = _parse_date(rec.get("cal_date"))
        if cal_date is None:
            continue
        rows.append(
            {
                "exchange": exchange,
                "cal_date": cal_date,
                "is_open": bool(int(rec.get("is_open") or 0)),
            }
        )
    count = await _upsert(session, TradeCalendar, rows, ["exchange", "cal_date"])
    logger.info("交易日历同步完成", exchange=exchange, rows=count, range=f"{start}~{end}")
    return count


async def sync_stock_basic(session: AsyncSession, client: TushareClient | None = None) -> int:
    """同步股票基础信息（名称 / 行业 / 上市日期）。"""
    client = client or get_tushare_client()
    df = await client.query("stock_basic", list_status="L")
    records = TushareClient.to_records(df)

    rows: list[dict[str, Any]] = []
    for rec in records:
        ts_code = rec.get("ts_code")
        if not ts_code:
            continue
        rows.append(
            {
                "ts_code": ts_code,
                "name": rec.get("name"),
                "industry": rec.get("industry"),
                "market": rec.get("market"),
                "list_date": _parse_date(rec.get("list_date")),
            }
        )
    count = await _upsert(session, StockBasic, rows, ["ts_code"])
    logger.info("股票基础信息同步完成", rows=count)
    return count


async def sync_daily(
    session: AsyncSession,
    trade_date: date | str,
    client: TushareClient | None = None,
) -> tuple[int, int]:
    """同步单个交易日的日线行情 + 每日指标，返回 (日线行数, 指标行数)。"""
    client = client or get_tushare_client()
    day = _parse_date(trade_date)
    if day is None:
        raise ValueError(f"非法交易日：{trade_date}")
    date_str = day.strftime("%Y%m%d")

    # --- 日线 ---
    df = await client.query("daily", trade_date=date_str)
    daily_rows: list[dict[str, Any]] = []
    for rec in TushareClient.to_records(df):
        ts_code = rec.get("ts_code")
        if not ts_code:
            continue
        daily_rows.append(
            {
                "ts_code": ts_code,
                "trade_date": day,
                "open": _f(rec.get("open")),
                "high": _f(rec.get("high")),
                "low": _f(rec.get("low")),
                "close": _f(rec.get("close")),
                "vol": _f(rec.get("vol")),
                "amount": _f(rec.get("amount")),
                "pct_chg": _f(rec.get("pct_chg")),
            }
        )
    daily_count = await _upsert(session, StockDaily, daily_rows, ["ts_code", "trade_date"])

    # --- 每日指标（估值/市值/换手） ---
    df_basic = await client.query("daily_basic", trade_date=date_str)
    basic_rows: list[dict[str, Any]] = []
    for rec in TushareClient.to_records(df_basic):
        ts_code = rec.get("ts_code")
        if not ts_code:
            continue
        basic_rows.append(
            {
                "ts_code": ts_code,
                "trade_date": day,
                "close": _f(rec.get("close")),
                "turnover_rate": _f(rec.get("turnover_rate")),
                "volume_ratio": _f(rec.get("volume_ratio")),
                "pe_ttm": _f(rec.get("pe_ttm")),
                "pb": _f(rec.get("pb")),
                "ps_ttm": _f(rec.get("ps_ttm")),
                "dv_ttm": _f(rec.get("dv_ttm")),
                "total_mv": _f(rec.get("total_mv")),
            }
        )
    basic_count = await _upsert(session, StockDailyBasic, basic_rows, ["ts_code", "trade_date"])

    logger.info(
        "日线同步完成",
        trade_date=date_str,
        daily=daily_count,
        basic=basic_count,
    )
    return daily_count, basic_count


async def sync_index_daily(
    session: AsyncSession,
    start_date: date | str,
    end_date: date | str,
    client: TushareClient | None = None,
    codes: dict[str, str] | None = None,
) -> int:
    """同步指数日线（逐个指数拉取，index_daily 一次只支持一个 ts_code）。"""
    client = client or get_tushare_client()
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start is None or end is None:
        raise ValueError(f"非法日期区间：{start_date} ~ {end_date}")

    targets = codes or INDEX_CODES
    total = 0
    for ts_code, name in targets.items():
        df = await client.query(
            "index_daily",
            ts_code=ts_code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        rows: list[dict[str, Any]] = []
        for rec in TushareClient.to_records(df):
            day = _parse_date(rec.get("trade_date"))
            if day is None:
                continue
            rows.append(
                {
                    "ts_code": ts_code,
                    "trade_date": day,
                    "name": name,
                    "close": _f(rec.get("close")),
                    "pct_chg": _f(rec.get("pct_chg")),
                    "amount": _f(rec.get("amount")),
                }
            )
        total += await _upsert(session, IndexDailyBar, rows, ["ts_code", "trade_date"])

    logger.info("指数日线同步完成", indexes=len(targets), rows=total)
    return total


# ----------------------------------------------------------------------
# 聚合 market_daily
# ----------------------------------------------------------------------
async def build_market_daily(session: AsyncSession, trade_date: date | str) -> MarketDaily | None:
    """从 stock_daily / stock_daily_basic / index_daily_bar 聚合出当日市场快照。

    产出：指数、涨跌平家数、涨跌停家数、成交额、板块涨跌 TOP/BOTTOM。
    """
    day = _parse_date(trade_date)
    if day is None:
        raise ValueError(f"非法交易日：{trade_date}")

    # 指数
    idx_rows = (
        await session.execute(
            select(IndexDailyBar).where(IndexDailyBar.trade_date == day)
        )
    ).scalars().all()
    index_bars = [
        {
            "code": r.ts_code,
            "name": r.name or INDEX_CODES.get(r.ts_code, r.ts_code),
            "close": float(r.close) if r.close is not None else None,
            "pct_chg": float(r.pct_chg) if r.pct_chg is not None else None,
            "amount": float(r.amount) if r.amount is not None else None,
        }
        for r in idx_rows
    ]

    # 涨跌平 / 涨跌停 / 成交额（amount 单位是千元，转亿元）
    # pct_chg 为 NULL 时 case 走 else_=0，不计入任何涨跌分类
    stats = (
        await session.execute(
            select(
                func.count().label("total"),
                func.sum(case((StockDaily.pct_chg > 0, 1), else_=0)).label("advance"),
                func.sum(case((StockDaily.pct_chg < 0, 1), else_=0)).label("decline"),
                func.sum(case((StockDaily.pct_chg == 0, 1), else_=0)).label("flat"),
                func.sum(case((StockDaily.pct_chg >= LIMIT_UP_PCT, 1), else_=0)).label("limit_up"),
                func.sum(case((StockDaily.pct_chg <= LIMIT_DOWN_PCT, 1), else_=0)).label("limit_down"),
                func.sum(StockDaily.amount).label("amount"),
            ).where(StockDaily.trade_date == day)
        )
    ).first()

    total = int(stats.total or 0) if stats else 0
    advance = int(stats.advance or 0) if stats else 0
    decline = int(stats.decline or 0) if stats else 0
    flat = int(stats.flat or 0) if stats else 0
    limit_up = int(stats.limit_up or 0) if stats else 0
    limit_down = int(stats.limit_down or 0) if stats else 0
    # amount 单位千元 → 亿元
    amount_yi = round(float(stats.amount or 0) / 100000, 2) if stats and stats.amount else 0.0

    # 板块涨跌：按 stock_basic.industry 聚合平均涨幅（取 TOP/BOTTOM 5，至少 3 只样本）
    sector_rows = (
        await session.execute(
            select(
                StockBasic.industry.label("industry"),
                func.avg(StockDaily.pct_chg).label("avg_pct"),
                func.count().label("cnt"),
            )
            .join(StockBasic, StockBasic.ts_code == StockDaily.ts_code)
            .where(
                StockDaily.trade_date == day,
                StockBasic.industry.is_not(None),
                StockDaily.pct_chg.is_not(None),
            )
            .group_by(StockBasic.industry)
            .having(func.count() >= 3)
        )
    ).all()
    sectors = [
        {"code": r.industry, "name": r.industry, "pct_chg": round(float(r.avg_pct or 0), 2), "count": int(r.cnt)}
        for r in sector_rows
    ]
    sectors_sorted = sorted(sectors, key=lambda x: x["pct_chg"], reverse=True)
    sectors_top = sectors_sorted[:5]
    sectors_bottom = sectors_sorted[-5:][::-1] if len(sectors_sorted) > 5 else []

    # 隔夜美股：us_daily 无权限，从近期资讯提取（降级方案）
    us_overnight = await build_us_overnight_from_news(session, day)

    stats_ready = total > 0 and len(index_bars) > 0

    values = {
        "is_trading_day": True,
        "index_bars": index_bars,
        "advance": advance,
        "decline": decline,
        "flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "total_amount": amount_yi,
        "sectors_top": sectors_top,
        "sectors_bottom": sectors_bottom,
        "us_overnight": us_overnight,
        "stats_ready": stats_ready,
    }

    stmt = pg_insert(MarketDaily).values(trade_date=day, **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["trade_date"],
        set_={**values, "updated_at": func.now()},
    )
    await session.execute(stmt)
    await session.flush()

    logger.info(
        "市场快照聚合完成",
        trade_date=str(day),
        stocks=total,
        advance=advance,
        decline=decline,
        amount_yi=amount_yi,
        sectors=len(sectors),
        ready=stats_ready,
    )
    return (
        await session.execute(select(MarketDaily).where(MarketDaily.trade_date == day))
    ).scalar_one_or_none()


async def build_us_overnight_from_news(session: AsyncSession, day: date) -> list[dict[str, Any]]:
    """隔夜美股降级方案：us_daily 无权限时，从近 2 天资讯里提取美股相关标题。

    只做关键词匹配，不虚构行情数字，字段里明确标注来源为「资讯」。
    """
    from datetime import datetime as dt

    from fin_news.models.news import NewsItem

    keywords = ("美股", "纳指", "纳斯达克", "道指", "标普", "隔夜")
    start_dt = dt.combine(day - timedelta(days=2), dt.min.time())
    end_dt = dt.combine(day, dt.max.time())

    rows = (
        await session.execute(
            select(NewsItem)
            .where(
                NewsItem.publish_time >= start_dt,
                NewsItem.publish_time <= end_dt,
                NewsItem.score >= 4,
            )
            .order_by(NewsItem.score.desc())
            .limit(30)
        )
    ).scalars().all()

    hits: list[dict[str, Any]] = []
    for news in rows:
        text = f"{news.title or ''}{news.content or ''}"
        if any(k in text for k in keywords):
            hits.append(
                {
                    "title": news.title,
                    "news_id": news.id,
                    "score": news.score,
                    "publish_time": news.publish_time.isoformat() if news.publish_time else None,
                    "source": "news",  # 明确标注：非行情接口，来自资讯
                }
            )
        if len(hits) >= 5:
            break

    if hits:
        logger.info("隔夜美股走资讯降级", trade_date=str(day), hits=len(hits))
    return hits


# ----------------------------------------------------------------------
# 编排
# ----------------------------------------------------------------------
async def sync_market_for_date(
    session: AsyncSession,
    trade_date: date | str,
    client: TushareClient | None = None,
) -> MarketDaily | None:
    """同步单个交易日的全部行情并聚合快照。"""
    day = _parse_date(trade_date)
    if day is None:
        raise ValueError(f"非法交易日：{trade_date}")

    await sync_daily(session, day, client)
    await sync_index_daily(session, day, day, client)
    return await build_market_daily(session, day)


async def sync_market_recent(
    session: AsyncSession,
    days: int = 7,
    client: TushareClient | None = None,
) -> list[str]:
    """同步最近 N 个交易日的行情（补齐历史，供盘后归因与个股走势使用）。"""
    client = client or get_tushare_client()
    today = date.today()
    trade_days = (
        await session.execute(
            select(TradeCalendar.cal_date)
            .where(
                TradeCalendar.is_open.is_(True),
                TradeCalendar.cal_date <= today,
            )
            .order_by(TradeCalendar.cal_date.desc())
            .limit(days)
        )
    ).scalars().all()

    if not trade_days:
        await sync_trade_calendar(session, client)
        trade_days = (
            await session.execute(
                select(TradeCalendar.cal_date)
                .where(TradeCalendar.is_open.is_(True), TradeCalendar.cal_date <= today)
                .order_by(TradeCalendar.cal_date.desc())
                .limit(days)
            )
        ).scalars().all()

    # 逐个交易日同步（由近及远）
    synced: list[str] = []
    for day in trade_days:
        try:
            await sync_daily(session, day, client)
            synced.append(day.isoformat())
        except Exception as exc:  # noqa: BLE001 - 单日失败不影响其他日期
            logger.warning("交易日行情同步失败", trade_date=str(day), error=str(exc)[:200])

    if synced:
        # 指数按区间一次性拉取，避免逐日多次调用
        start = min(trade_days)
        end = max(trade_days)
        await sync_index_daily(session, start, end, client)
        for day in trade_days:
            try:
                await build_market_daily(session, day)
            except Exception as exc:  # noqa: BLE001
                logger.warning("市场快照聚合失败", trade_date=str(day), error=str(exc)[:200])

    logger.info("近期行情同步完成", days=len(synced), dates=synced[:5])
    return synced
