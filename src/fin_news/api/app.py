"""FastAPI 应用装配。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fin_news.api.errors import register_exception_handlers
from fin_news.api.routers import admin, analysis, chat, discovery, market, news, system
from fin_news.core.config import Settings, get_settings
from fin_news.core.db import dispose_engine, init_db
from fin_news.core.logging import configure_logging, get_logger

logger = get_logger("api.app")


def create_app(settings: Settings | None = None, with_background: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_db()
        stop_event: asyncio.Event | None = None
        worker_task: asyncio.Task | None = None
        scheduler = None

        if with_background and settings.env != "test":
            from fin_news.ingestion.scheduler import build_scheduler
            from fin_news.pipeline.worker import PipelineWorker

            scheduler = build_scheduler(settings)
            scheduler.start()
            logger.info("调度器已启动", jobs=len(scheduler.get_jobs()))

            stop_event = asyncio.Event()
            worker = PipelineWorker(settings)
            worker_task = asyncio.create_task(worker.run_forever(stop_event))
            app.state.pipeline_worker = worker

        app.state.scheduler = scheduler
        try:
            yield
        finally:
            if worker_task and stop_event:
                stop_event.set()
                try:
                    await asyncio.wait_for(worker_task, timeout=30)
                except TimeoutError:
                    logger.warning("Pipeline worker 退出超时，强制取消")
                    worker_task.cancel()
            if scheduler:
                scheduler.shutdown(wait=False)
                logger.info("调度器已停止")
            await dispose_engine()

    app = FastAPI(
        title="fin-news-v5 API",
        version="0.1.0",
        description="财经资讯分析 Agent：解释市场为什么涨跌，并支持持续追问。",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    prefix = settings.api_prefix
    app.include_router(news.router, prefix=prefix)
    app.include_router(discovery.router, prefix=prefix)
    app.include_router(analysis.router, prefix=prefix)
    app.include_router(market.router, prefix=prefix)
    app.include_router(chat.router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)
    app.include_router(system.router, prefix=prefix)
    return app


app = create_app(with_background=False)
