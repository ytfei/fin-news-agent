"""APScheduler 装配：分钟级增量接入 + 盘前/盘后定时任务 + 事件清理。"""
from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from fin_news.core.config import Settings, get_settings
from fin_news.core.logging import get_logger

logger = get_logger("ingestion.scheduler")


def build_scheduler(settings: Settings | None = None) -> AsyncIOScheduler:
    settings = settings or get_settings()
    scheduler = AsyncIOScheduler(
        timezone="Asia/Shanghai",
        job_defaults={
            "coalesce": True,          # 错失的多次触发合并为一次
            "max_instances": 1,        # 同一任务不并发
            "misfire_grace_time": 120, # 错失 120s 内仍补跑
        },
    )

    # ---------------- 分钟级增量接入 ----------------
    for source_key in settings.news_sources:
        scheduler.add_job(
            _job_ingest,
            trigger=IntervalTrigger(seconds=settings.ingest_interval_seconds),
            id=f"ingest:{source_key}",
            name=f"增量接入 {source_key}",
            args=[source_key],
            replace_existing=True,
        )

    # ---------------- 盘前 / 盘后（交易日） ----------------
    scheduler.add_job(
        _job_pre_market,
        trigger=CronTrigger(
            hour=settings.pre_market_hour,
            minute=settings.pre_market_minute,
            day_of_week="mon-fri",
        ),
        id="market:pre",
        name="盘前展望",
        replace_existing=True,
    )
    scheduler.add_job(
        _job_post_market,
        trigger=CronTrigger(
            hour=settings.post_market_hour,
            minute=settings.post_market_minute,
            day_of_week="mon-fri",
        ),
        id="market:post",
        name="盘后复盘",
        replace_existing=True,
    )

    # ---------------- 运维 ----------------
    scheduler.add_job(
        _job_cleanup,
        trigger=CronTrigger(hour=3, minute=10),
        id="ops:cleanup",
        name="清理过期事件",
        replace_existing=True,
    )
    return scheduler


# ----------------------------------------------------------------------
# 任务体（延迟导入，避免循环依赖）


async def _job_ingest(source_key: str | None = None) -> None:
    from fin_news.ingestion.service import IngestionService

    service = IngestionService()
    try:
        if source_key:
            source = next((s for s in service.sources if s.source_key == source_key), None)
            if source is None:
                logger.warning("未找到数据源", source_key=source_key)
                return
            results = [await service.run_source(source)]
        else:
            results = await service.run_all()
        for r in results:
            if r.status != "OK":
                logger.warning("接入任务异常", source_key=r.source_key, status=r.status, message=r.message)
    except Exception as exc:  # noqa: BLE001
        logger.exception("接入任务失败", error=str(exc))


async def _job_pre_market() -> None:
    from fin_news.agents.market_agents import run_pre_market

    try:
        await run_pre_market()
    except Exception as exc:  # noqa: BLE001
        logger.exception("盘前任务失败", error=str(exc))


async def _job_post_market() -> None:
    from fin_news.agents.market_agents import run_post_market

    try:
        await run_post_market()
    except Exception as exc:  # noqa: BLE001
        logger.exception("盘后任务失败", error=str(exc))


async def _job_cleanup() -> None:
    from fin_news.core.db import session_scope
    from fin_news.events.bus import EventBus

    try:
        async with session_scope() as session:
            await EventBus(session).cleanup_done()
    except Exception as exc:  # noqa: BLE001
        logger.exception("清理任务失败", error=str(exc))
