"""Pipeline worker：消费事件 → 评分 → 向量化 → 深度分析。

支持多副本：事件用 FOR UPDATE SKIP LOCKED 锁定，不会重复消费。
"""
from __future__ import annotations

import asyncio
import socket
from collections import defaultdict

from fin_news.core.config import Settings, get_settings
from fin_news.core.db import session_scope
from fin_news.core.enums import EventType
from fin_news.core.logging import get_logger
from fin_news.events.bus import EventBus
from fin_news.models.event import IngestEvent
from fin_news.pipeline.batching import Batcher
from fin_news.pipeline.handlers import HANDLERS

logger = get_logger("pipeline.worker")

# 需要攒批的事件类型（批量评分），其余事件立即处理
BATCHED_EVENTS = {EventType.NEWS_INGESTED}


class PipelineWorker:
    def __init__(self, settings: Settings | None = None, worker_id: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.worker_id = worker_id or f"{socket.gethostname()}-{id(self) % 10000}"
        self._running = False
        self._batchers: dict[str, Batcher[IngestEvent]] = {}

    # ------------------------------------------------------------------
    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        self._running = True
        await self.reclaim()
        logger.info("Pipeline worker 启动", worker_id=self.worker_id)

        try:
            while self._running and not (stop_event and stop_event.is_set()):
                try:
                    processed = await self.tick()
                except Exception as exc:  # noqa: BLE001 - worker 不能因为单批失败退出
                    logger.exception("事件处理异常", error=str(exc))
                    processed = 0
                if stop_event and stop_event.is_set():
                    break
                await asyncio.sleep(
                    self.settings.worker_poll_interval_seconds if not processed else 0.2
                )
        finally:
            await self.flush()
            self._running = False
            logger.info("Pipeline worker 已停止", worker_id=self.worker_id)

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    async def reclaim(self) -> None:
        """启动时回收上次异常退出遗留的 PROCESSING 事件。"""
        async with session_scope() as session:
            await EventBus(session, self.worker_id).reclaim_stale(minutes=10)

    async def tick(self) -> int:
        """拉取一批事件并处理，返回处理条数。"""
        # 拉取事件单独一个事务，处理阶段按事件类型分组各自开事务
        async with session_scope() as session:
            bus = EventBus(session, self.worker_id)
            events = list(await bus.poll(self.settings.worker_batch_limit))
        if not events:
            return 0

        grouped: dict[str, list[IngestEvent]] = defaultdict(list)
        for event in events:
            grouped[event.event_type].append(event)

        processed = 0
        for event_type, group in grouped.items():
            batch = self._take_batch(event_type, group)
            if not batch:
                continue

            handler = HANDLERS.get(event_type)
            try:
                async with session_scope() as group_session:
                    group_bus = EventBus(group_session, self.worker_id)
                    if handler is None:
                        logger.warning("无对应处理器", event_type=event_type)
                        for event in batch:
                            await group_bus.ack(event)
                    else:
                        await handler(group_session, batch, group_bus, self.settings)
            except Exception as exc:  # noqa: BLE001
                # 处理器抛异常时不能让 worker 主循环退出，
                # 否则事件会一直卡在 PROCESSING 直到 reclaim
                logger.exception(
                    "事件处理异常", event_type=event_type, count=len(batch), error=str(exc)[:300]
                )
                async with session_scope() as err_session:
                    err_bus = EventBus(err_session, self.worker_id)
                    for event in batch:
                        await err_bus.fail(event, str(exc)[:300], error_type="HandlerCrash")
            processed += len(batch)

        # 未达到触发条件的事件留在攒批器里，等下一轮
        return processed

    async def flush(self) -> None:
        """优雅退出：把攒批器中未处理的事件放回队列。"""
        if not self._batchers:
            return
        async with session_scope() as session:
            bus = EventBus(session, self.worker_id)
            for batcher in self._batchers.values():
                pending = batcher.take_all()
                if pending:
                    await bus.release(pending)
        self._batchers.clear()

    # ------------------------------------------------------------------
    def _take_batch(self, event_type: str, events: list[IngestEvent]) -> list[IngestEvent]:
        try:
            et = EventType(event_type)
        except ValueError:
            return events

        if et not in BATCHED_EVENTS:
            return events

        batcher = self._batchers.setdefault(
            event_type,
            Batcher(self.settings.scoring_batch_size, self.settings.scoring_window_seconds),
        )
        batcher.add(events)
        if batcher.ready():
            return batcher.take()
        return []


async def run_worker_forever(stop_event: asyncio.Event | None = None) -> None:
    await PipelineWorker().run_forever(stop_event)
