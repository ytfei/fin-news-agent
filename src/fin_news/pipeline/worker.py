"""Pipeline worker：消费事件 → 评分 → 向量化 → 深度分析。

支持多副本：事件用 FOR UPDATE SKIP LOCKED 锁定，不会重复消费。
"""
from __future__ import annotations

import asyncio
import socket
import time
from collections import defaultdict

from fin_news.core.config import Settings, get_settings
from fin_news.core.db import session_scope
from fin_news.core.enums import EventType
from fin_news.core.logging import bind_context, elapsed_ms, get_logger, unbind_context
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
        # 本次运行的累计计数（结束时汇总打印，便于判断跑了多少、失败多少）
        self.stats: dict[str, int] = {
            "ticks": 0,
            "processed": 0,
            "failed": 0,
            "reclaimed": 0,
            "released": 0,
        }

    # ------------------------------------------------------------------
    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        self._running = True
        started = time.perf_counter()
        bind_context(worker_id=self.worker_id)
        await self.reclaim()
        logger.info(
            "Pipeline worker 启动",
            worker_id=self.worker_id,
            poll_interval_seconds=self.settings.worker_poll_interval_seconds,
            batch_limit=self.settings.worker_batch_limit,
            scoring_batch_size=self.settings.scoring_batch_size,
            scoring_window_seconds=self.settings.scoring_window_seconds,
            agent_framework=self.settings.agent_framework,
        )

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
            _ = await self.flush()  # 放回攒批器里未处理的事件
            self._running = False
            logger.info(
                "Pipeline worker 已停止",
                worker_id=self.worker_id,
                elapsed_ms=elapsed_ms(started),
                **self.stats,
            )

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    async def reclaim(self) -> None:
        """启动时回收上次异常退出遗留的 PROCESSING 事件。"""
        started = time.perf_counter()
        async with session_scope() as session:
            reclaimed = await EventBus(session, self.worker_id).reclaim_stale(minutes=10)
        self.stats["reclaimed"] += int(reclaimed or 0)
        logger.info(
            "回收超时事件完成", reclaimed=int(reclaimed or 0), elapsed_ms=elapsed_ms(started)
        )

    async def tick(self) -> int:
        """拉取一批事件并处理，返回处理条数。"""
        self.stats["ticks"] += 1
        # 先处理攒批器里已满足触发条件的批（不依赖新 poll 到的事件，否则新闻源
        # 暂时无新数据时，攒批器里已 ready 的事件会一直卡在 PROCESSING）
        processed = await self._drain_ready_batches()

        # 拉取事件单独一个事务，处理阶段按事件类型分组各自开事务
        started = time.perf_counter()
        async with session_scope() as session:
            bus = EventBus(session, self.worker_id)
            events = list(await bus.poll(self.settings.worker_batch_limit))
        if not events:
            logger.debug("本轮无待处理事件", tick=self.stats["ticks"], elapsed_ms=elapsed_ms(started))
            return processed

        grouped: dict[str, list[IngestEvent]] = defaultdict(list)
        for event in events:
            grouped[event.event_type].append(event)
        by_type = {event_type: len(group) for event_type, group in grouped.items()}
        logger.info(
            "拉取事件",
            tick=self.stats["ticks"],
            count=len(events),
            by_type=by_type,
            elapsed_ms=elapsed_ms(started),
        )

        for event_type, group in grouped.items():
            batch = self._take_batch(event_type, group)
            if not batch:
                logger.debug(
                    "事件进入攒批器，等待凑批",
                    event_type=event_type,
                    pending=self._batchers[event_type].size,
                    batch_size=self.settings.scoring_batch_size,
                )
                continue
            processed += await self._process(event_type, batch)

        # 未达到触发条件的事件留在攒批器里，等下一轮
        return processed

    async def _drain_ready_batches(self) -> int:
        """处理攒批器里已满足触发条件（条数满 / 窗口到）的批，返回处理条数。"""
        processed = 0
        for event_type in list(self._batchers):
            batcher = self._batchers[event_type]
            if not batcher.ready():
                continue
            processed += await self._process(event_type, batcher.take())
            if not batcher.size:
                self._batchers.pop(event_type, None)
        return processed

    async def _process(self, event_type: str, batch: list[IngestEvent]) -> int:
        """调用 handler 处理一批事件；异常时逐条 fail（退避/死信）。返回处理条数。"""
        handler = HANDLERS.get(event_type)
        started = time.perf_counter()
        logger.info(
            "事件批次开始",
            event_type=event_type,
            count=len(batch),
            handler=getattr(handler, "__module__", ""),
            event_ids=[e.id for e in batch][:20],
        )
        # 绑定到上下文：handler 内部（含 agent）的日志自动带 event_type，
        # 便于按 run_id + event_type 还原一次运行的完整路径
        bind_context(event_type=event_type)
        try:
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
                self.stats["failed"] += len(batch)
                logger.exception(
                    "事件处理异常",
                    event_type=event_type,
                    count=len(batch),
                    elapsed_ms=elapsed_ms(started),
                    error=str(exc)[:300],
                )
                async with session_scope() as err_session:
                    err_bus = EventBus(err_session, self.worker_id)
                    for event in batch:
                        await err_bus.fail(event, str(exc)[:300], error_type="HandlerCrash")
                return len(batch)

            self.stats["processed"] += len(batch)
            logger.info(
                "事件批次结束",
                event_type=event_type,
                count=len(batch),
                elapsed_ms=elapsed_ms(started),
            )
            return len(batch)
        finally:
            unbind_context("event_type")

    async def flush(self) -> int:
        """优雅退出：把攒批器中未处理的事件放回队列。返回放回条数。"""
        if not self._batchers:
            return 0
        released = 0
        async with session_scope() as session:
            bus = EventBus(session, self.worker_id)
            for event_type, batcher in self._batchers.items():
                pending = batcher.take_all()
                if pending:
                    await bus.release(pending)
                    released += len(pending)
                    logger.info("攒批事件放回队列", event_type=event_type, count=len(pending))
        self._batchers.clear()
        self.stats["released"] += released
        return released

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
