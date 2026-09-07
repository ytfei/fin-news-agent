"""Pipeline worker 的「慢事件后台化」行为测试。

背景：worker 主循环是串行的（`await tick()`），而深度分析单批可达数十分钟，
会把整个消费循环堵死 —— 表现为「agent 一直在跑，新同步的资讯却迟迟不评分」。

修复是把 `news.embedded` 这类慢事件转到后台任务，主循环立即继续 poll。
这里锁定三条不变量：

* 慢事件转后台后 `tick()` **立即返回**，不等分析跑完
* 后台任务达上限时**回退同步执行**，避免任务无限堆积
* 快事件（评分 / 向量化）仍**同步**处理，行为与改动前一致
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import pytest

from fin_news.core.config import Settings
from fin_news.core.enums import EventType
from fin_news.pipeline import worker as worker_mod
from fin_news.pipeline.worker import MAX_SLOW_TASKS, PipelineWorker


class _FakeEvent:
    def __init__(self, event_id: int, event_type: str) -> None:
        self.id = event_id
        self.event_type = event_type
        self.aggregate_id = event_id


def _settings(**kw) -> Settings:
    base = dict(worker_batch_limit=50, scoring_batch_size=30)
    base.update(kw)
    return Settings(_env_file=None, **base)


def _patch_bus(monkeypatch, events: list[_FakeEvent]) -> None:
    """让 poll 返回指定事件；其余方法用不着的空实现。"""

    class _Bus:
        def __init__(self, session, worker_id: str = "w") -> None:
            pass

        async def poll(self, limit: int | None = None):
            return events

    monkeypatch.setattr(worker_mod, "EventBus", _Bus)

    @asynccontextmanager
    async def _scope():
        yield object()

    monkeypatch.setattr(worker_mod, "session_scope", _scope)


@pytest.fixture
def cleanup_tasks():
    """测试结束后取消残留的后台任务，避免 asyncio 警告。"""
    pending: list[asyncio.Task] = []
    yield pending
    for t in pending:
        t.cancel()


async def test_slow_event_runs_in_background(monkeypatch):
    """慢事件转后台：tick 立即返回，不等待分析完成。"""
    w = PipelineWorker(_settings())
    handled: list[str] = []

    async def slow_process(event_type, batch):
        handled.append(event_type)
        await asyncio.sleep(0.5)  # 模拟慢分析
        return len(batch)

    monkeypatch.setattr(w, "_process", slow_process)
    _patch_bus(monkeypatch, [_FakeEvent(1, EventType.NEWS_EMBEDDED.value)])

    started = time.perf_counter()
    processed = await w.tick()
    elapsed = time.perf_counter() - started

    assert processed == 0, "慢事件转后台后本轮不计入 processed"
    assert elapsed < 0.3, f"tick 不应等待慢分析完成，实际耗时 {elapsed:.2f}s"
    assert len(w._slow_tasks) == 1, "应创建一个后台任务"

    # 后台任务确实在跑，并最终完成处理
    await asyncio.sleep(0.6)
    assert handled == [EventType.NEWS_EMBEDDED.value]


async def test_slow_falls_back_to_sync_at_limit(monkeypatch):
    """后台任务达上限时回退同步执行，避免任务无限堆积。"""
    w = PipelineWorker(_settings())
    blockers = {asyncio.create_task(asyncio.sleep(5)) for _ in range(MAX_SLOW_TASKS)}
    w._slow_tasks |= blockers
    try:
        monkeypatch.setattr(w, "_process", lambda et, batch: asyncio.sleep(0, len(batch)))
        _patch_bus(monkeypatch, [_FakeEvent(1, EventType.NEWS_EMBEDDED.value)])

        processed = await w.tick()

        assert processed == 1, "达上限应回退同步执行并计入 processed"
        assert len(w._slow_tasks) == MAX_SLOW_TASKS, "不应再新增后台任务"
    finally:
        for t in blockers:
            t.cancel()


async def test_fast_event_still_processed_inline(monkeypatch):
    """快事件（评分链路）仍同步处理，行为与改动前一致。"""
    w = PipelineWorker(_settings())
    handled: list[str] = []

    async def fast_process(event_type, batch):
        handled.append(event_type)
        return len(batch)

    monkeypatch.setattr(w, "_process", fast_process)
    # news.scored 不属于 BATCHED_EVENTS / SLOW_EVENTS，走同步路径
    _patch_bus(monkeypatch, [_FakeEvent(1, EventType.NEWS_SCORED.value)])

    processed = await w.tick()

    assert processed == 1, "快事件应同步处理并计入 processed"
    assert handled == [EventType.NEWS_SCORED.value]
    assert not w._slow_tasks, "快事件不应产生后台任务"


async def test_slow_and_fast_in_same_tick(monkeypatch):
    """同一轮里慢事件转后台、快事件同步处理，互不阻塞。"""
    w = PipelineWorker(_settings())
    handled: list[str] = []

    async def process(event_type, batch):
        if event_type == EventType.NEWS_EMBEDDED.value:
            await asyncio.sleep(0.5)
        handled.append(event_type)
        return len(batch)

    monkeypatch.setattr(w, "_process", process)
    _patch_bus(
        monkeypatch,
        [
            _FakeEvent(1, EventType.NEWS_EMBEDDED.value),
            _FakeEvent(2, EventType.NEWS_SCORED.value),
        ],
    )

    started = time.perf_counter()
    processed = await w.tick()
    elapsed = time.perf_counter() - started

    assert processed == 1, "只有快事件计入本轮 processed"
    assert elapsed < 0.3, f"慢事件不应拖慢本轮，实际耗时 {elapsed:.2f}s"
    assert EventType.NEWS_SCORED.value in handled, "快事件本轮即完成"

    await asyncio.sleep(0.6)
    assert EventType.NEWS_EMBEDDED.value in handled, "慢事件在后台完成"
