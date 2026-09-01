"""APScheduler 装配：分钟级接入 / 行情同步 / 盘前盘后简报。

任务清单：

| id | 触发 | 说明 |
| --- | --- | --- |
| `ingest` | 每 `ingest_interval_seconds`（默认 60s） | 增量拉取资讯并发事件 |
| `market_sync` | 每日 16:00（Asia/Shanghai） | 同步最近交易日行情 + 聚合快照 |
| `pre_market` | 交易日 `pre_market_hour:minute`（默认 07:30） | 生成盘前展望简报 |
| `post_market` | 交易日 `post_market_hour:minute`（默认 15:30） | 生成盘后复盘简报 |

说明：
* 事件消费（pipeline worker）不在这里调度，而是由 `main.py` 以常驻 asyncio 任务
  运行（它需要持续轮询而不是定时触发）。
* 盘前/盘后会先查 `trade_calendar` 判断是否交易日，非交易日跳过。
* 所有任务 `coalesce=True / max_instances=1`，避免重叠触发。
"""
from __future__ import annotations

from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from fin_news.core.config import Settings, get_settings
from fin_news.core.logging import get_logger

logger = get_logger("ingestion.scheduler")

TZ = "Asia/Shanghai"

# 行情同步时间（收盘后，Tushare 数据通常 15:30 后陆续就绪）
MARKET_SYNC_HOUR = 16
MARKET_SYNC_MINUTE = 0

_scheduler: AsyncIOScheduler | None = None


# ----------------------------------------------------------------------
# 任务实现（延迟导入，避免 import 期就把 DB / 模型都拉起来）
# ----------------------------------------------------------------------
async def job_ingest() -> None:
    """分钟级增量接入。"""
    from fin_news.core.db import init_db
    from fin_news.ingestion.service import IngestionService

    await init_db()
    results = await IngestionService().run_all()
    if any(r.status != "OK" for r in results):
        logger.warning(
            "接入存在异常源",
            not_ok=[r.source_key for r in results if r.status != "OK"],
        )


async def job_sync_market() -> None:
    """每日收盘后同步行情（最近 3 个交易日，补齐假期/失败缺口）。"""
    from fin_news.core.db import init_db, session_scope
    from fin_news.ingestion.sources.tushare_market import sync_market_recent, sync_stock_basic

    await init_db()
    async with session_scope() as session:
        try:
            await sync_stock_basic(session)
        except Exception as exc:  # noqa: BLE001 - 基础信息失败不阻塞行情
            logger.warning("股票基础信息同步失败", error=str(exc)[:200])
        await sync_market_recent(session, days=3)


async def _today_is_trading_day() -> bool:
    from fin_news.core.db import init_db, session_scope
    from fin_news.agents.tools.market_data import is_trading_day

    await init_db()
    async with session_scope() as session:
        return await is_trading_day(session, date.today())


async def job_pre_market() -> None:
    """交易日盘前简报。"""
    from fin_news.core.db import init_db
    from fin_news.agents.market_agents import run_pre_market

    if not await _today_is_trading_day():
        logger.info("非交易日，跳过盘前简报", trade_date=str(date.today()))
        return

    await init_db()
    report = await run_pre_market(date.today())
    if report is None:
        logger.warning("盘前简报未生成", trade_date=str(date.today()))
        return
    logger.info("盘前简报已生成", trade_date=str(date.today()), report_id=report.id)


async def job_post_market() -> None:
    """交易日盘后复盘简报。"""
    from fin_news.core.db import init_db
    from fin_news.agents.market_agents import run_post_market

    if not await _today_is_trading_day():
        logger.info("非交易日，跳过盘后简报", trade_date=str(date.today()))
        return

    await init_db()
    report = await run_post_market(date.today())
    if report is None:
        logger.warning("盘后简报未生成", trade_date=str(date.today()))
        return
    logger.info("盘后简报已生成", trade_date=str(date.today()), report_id=report.id)


# ----------------------------------------------------------------------
# 装配
# ----------------------------------------------------------------------
def build_scheduler(settings: Settings | None = None) -> AsyncIOScheduler:
    settings = settings or get_settings()
    scheduler = AsyncIOScheduler(
        timezone=TZ,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 300,  # 容忍 5 分钟内的错失触发
        },
    )

    scheduler.add_job(
        job_ingest,
        trigger=IntervalTrigger(seconds=settings.ingest_interval_seconds),
        id="ingest",
        name="分钟级增量接入",
        replace_existing=True,
    )
    scheduler.add_job(
        job_sync_market,
        trigger=CronTrigger(hour=MARKET_SYNC_HOUR, minute=MARKET_SYNC_MINUTE, timezone=TZ),
        id="market_sync",
        name="行情数据同步",
        replace_existing=True,
    )
    scheduler.add_job(
        job_pre_market,
        trigger=CronTrigger(
            hour=settings.pre_market_hour, minute=settings.pre_market_minute, timezone=TZ
        ),
        id="pre_market",
        name="盘前简报",
        replace_existing=True,
    )
    scheduler.add_job(
        job_post_market,
        trigger=CronTrigger(
            hour=settings.post_market_hour, minute=settings.post_market_minute, timezone=TZ
        ),
        id="post_market",
        name="盘后简报",
        replace_existing=True,
    )

    logger.info(
        "调度器已装配",
        tz=TZ,
        ingest_interval_seconds=settings.ingest_interval_seconds,
        market_sync=f"{MARKET_SYNC_HOUR:02d}:{MARKET_SYNC_MINUTE:02d}",
        pre_market=f"{settings.pre_market_hour:02d}:{settings.pre_market_minute:02d}",
        post_market=f"{settings.post_market_hour:02d}:{settings.post_market_minute:02d}",
    )
    return scheduler


def get_scheduler(settings: Settings | None = None) -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = build_scheduler(settings)
    return _scheduler


def start_scheduler(settings: Settings | None = None) -> AsyncIOScheduler:
    scheduler = get_scheduler(settings)
    if not scheduler.running:
        scheduler.start()
        logger.info("调度器已启动", jobs=len(scheduler.get_jobs()))
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("调度器已停止")
    _scheduler = None
