"""调度器独立进程入口。

    uv run python -m fin_news.scheduler

与 `fin_news.main`（API + PipelineWorker）分离。多 worker 部署时，定时任务
（ingest / market_sync / pre_market / post_market / expire_stale_analysis）只应由
**一个**进程执行，否则会重复拉取资讯、重复生成简报。因此把调度器拆成独立进程，
部署时单副本运行；`main` 只承载 API 与事件消费 worker，可任意多副本。

Docker 用法（Dockerfile 的 ENTRYPOINT 会先跑 alembic 迁移，CMD 决定进程）：
    docker run <image>                                    # 默认 main
    docker run <image> python -m fin_news.scheduler       # 覆盖 CMD 跑调度器
"""
from __future__ import annotations

import asyncio
import signal

from fin_news.core.config import get_settings
from fin_news.core.db import init_db
from fin_news.core.logging import configure_logging, get_logger
from fin_news.ingestion.scheduler import shutdown_scheduler, start_scheduler

logger = get_logger("scheduler")


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    await init_db()

    start_scheduler(settings)
    logger.info("调度器进程已启动", env=settings.env)

    # 保持进程存活，直到收到 SIGINT / SIGTERM（docker stop 发 SIGTERM）
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows 无 add_signal_handler
            pass

    try:
        await stop.wait()
    finally:
        shutdown_scheduler()
        logger.info("调度器进程已退出")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
