"""分析产物与行情缓存：analysis_report / market_daily / 行情表 / 板块 / 交易日历。"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from fin_news.core.enums import AgentType, IngestKind, MarketPeriod, ReportStatus, ScoreBand
from fin_news.models.base import Base, PublicIdMixin, TimestampMixin, fk, pg_enum

agent_type_t = pg_enum(AgentType, "agent_type")
report_status_t = pg_enum(ReportStatus, "report_status")
score_band_t = pg_enum(ScoreBand, "score_band")
period_t = pg_enum(MarketPeriod, "market_period")


class AnalysisReport(Base, PublicIdMixin, TimestampMixin):
    """统一的分析报告：宏观 / 行业 / 个股 / 盘前 / 盘后 / 追问引用。"""

    __tablename__ = "analysis_report"
    __table_args__ = (
        Index(
            "uq_report_news_agent",
            "news_id",
            "agent_type",
            "prompt_version",
            unique=True,
            postgresql_where="status IN ('DRAFT','PUBLISHED','DEGRADED')",
        ),
        Index(
            "uq_report_brief",
            "trade_date",
            "period",
            "prompt_version",
            unique=True,
            # status 过滤是关键：简报重跑时旧报告先被标为 SUPERSEDED，
            # 若不加 status 条件，SUPERSEDED 的旧记录仍占据唯一索引，导致新简报插入失败
            postgresql_where=(
                "period IN ('pre_market','post_market')"
                " AND status IN ('DRAFT','PUBLISHED','DEGRADED')"
            ),
        ),
        Index("idx_report_pub", "published_at"),
        Index("idx_report_type_time", "agent_type", "published_at"),
        Index("idx_report_band", "band", "published_at"),
        Index("idx_report_entities", "entities", postgresql_using="gin"),
        Index("idx_report_trade_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    agent_type: Mapped[AgentType] = mapped_column(agent_type_t, nullable=False)
    news_id: Mapped[int | None] = mapped_column(BigInteger, fk("news_item.id", "SET NULL"))
    trade_date: Mapped[date | None] = mapped_column(Date)
    period: Mapped[MarketPeriod | None] = mapped_column(period_t)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    score: Mapped[int | None] = mapped_column(SmallInteger)
    band: Mapped[ScoreBand | None] = mapped_column(score_band_t)
    sentiment: Mapped[str | None] = mapped_column(String(16))
    impact_level: Mapped[str | None] = mapped_column(String(16))
    horizon: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float | None] = mapped_column()

    beneficiaries: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    victims: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    entities: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    references: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    external_sources: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)

    status: Mapped[ReportStatus] = mapped_column(
        report_status_t, nullable=False, default=ReportStatus.DRAFT
    )
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    run_id: Mapped[str | None] = mapped_column(String(64))

    tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_cent: Mapped[float | None] = mapped_column()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketDaily(Base, TimestampMixin):
    """每日市场快照：盘前 / 盘后 Agent 的输入缓存。"""

    __tablename__ = "market_daily"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_trading_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    index_bars: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    advance: Mapped[int | None] = mapped_column(Integer)
    decline: Mapped[int | None] = mapped_column(Integer)
    flat: Mapped[int | None] = mapped_column(Integer)
    limit_up: Mapped[int | None] = mapped_column(Integer)
    limit_down: Mapped[int | None] = mapped_column(Integer)
    total_amount: Mapped[float | None] = mapped_column(Numeric(18, 2))

    sectors_top: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    sectors_bottom: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    us_overnight: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    news_highlights: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)

    stats_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class StockDaily(Base):
    """日线行情缓存（tushare daily）。"""

    __tablename__ = "stock_daily"
    __table_args__ = (Index("idx_stock_daily_date", "trade_date"),)

    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Numeric(12, 4))
    high: Mapped[float | None] = mapped_column(Numeric(12, 4))
    low: Mapped[float | None] = mapped_column(Numeric(12, 4))
    close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    pct_chg: Mapped[float | None] = mapped_column(Float)
    vol: Mapped[float | None] = mapped_column(Numeric(20, 4))
    amount: Mapped[float | None] = mapped_column(Numeric(20, 4))


class StockDailyBasic(Base):
    """每日指标：估值与市值（tushare daily_basic）。"""

    __tablename__ = "stock_daily_basic"
    __table_args__ = (Index("idx_basic_date", "trade_date"),)

    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    turnover_rate: Mapped[float | None] = mapped_column(Float)
    volume_ratio: Mapped[float | None] = mapped_column(Float)
    pe_ttm: Mapped[float | None] = mapped_column(Float)
    pb: Mapped[float | None] = mapped_column(Float)
    ps_ttm: Mapped[float | None] = mapped_column(Float)
    dv_ttm: Mapped[float | None] = mapped_column(Float)
    total_mv: Mapped[float | None] = mapped_column(Numeric(20, 4))


class IndexDailyBar(Base):
    """指数日线（tushare index_daily）。"""

    __tablename__ = "index_daily_bar"

    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(32))
    close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    pct_chg: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Numeric(20, 4))


class USDailyBar(Base):
    """美股日线（tushare us_daily）。"""

    __tablename__ = "us_daily_bar"

    ts_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(64))
    close: Mapped[float | None] = mapped_column(Numeric(14, 4))
    pct_chg: Mapped[float | None] = mapped_column(Float)
    pe: Mapped[float | None] = mapped_column(Float)
    pb: Mapped[float | None] = mapped_column(Float)
    total_mv: Mapped[float | None] = mapped_column(Numeric(20, 4))


class TopListBar(Base):
    """龙虎榜明细（tushare top_list）。"""

    __tablename__ = "top_list_bar"
    __table_args__ = (Index("idx_lhb_date", "trade_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str | None] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(String(32))
    close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    pct_chg: Mapped[float | None] = mapped_column(Float)
    net_amount: Mapped[float | None] = mapped_column(Numeric(20, 4))
    reason: Mapped[str | None] = mapped_column(String(64))


class StockForecast(Base):
    """业绩预告（tushare stk_forecast / forecast）。"""

    __tablename__ = "stock_forecast"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_code: Mapped[str | None] = mapped_column(String(16))
    ann_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    type: Mapped[str | None] = mapped_column(String(16))
    p_change_min: Mapped[float | None] = mapped_column(Float)
    p_change_max: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[str | None] = mapped_column(Text)


class StockBasic(Base):
    """股票基础信息（名称、行业映射）。"""

    __tablename__ = "stock_basic"

    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(32))
    industry: Mapped[str | None] = mapped_column(String(32))
    market: Mapped[str | None] = mapped_column(String(16))
    list_date: Mapped[date | None] = mapped_column(Date)


class Sector(Base):
    """板块 / 概念。"""

    __tablename__ = "sector"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str | None] = mapped_column(String(16))


class SectorMember(Base):
    """板块成分。"""

    __tablename__ = "sector_member"

    sector_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(32))


class TradeCalendar(Base):
    """交易日历（tushare trade_cal）。"""

    __tablename__ = "trade_calendar"

    exchange: Mapped[str] = mapped_column(String(8), primary_key=True)
    cal_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class IngestCursor(Base, TimestampMixin):
    """增量接入位点。"""

    __tablename__ = "ingest_cursor"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    kind: Mapped[IngestKind] = mapped_column(
        pg_enum(IngestKind, "ingest_kind"),
        nullable=False,
        default=IngestKind.NEWS,
    )
    cursor_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    overlap_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_count: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    extra: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
    )
