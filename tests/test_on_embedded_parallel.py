"""on_embedded 深度分析的并发行为测试。

背景：`on_embedded.handle` 原先串行处理整批事件（单条 70~360 秒，一批 50 条需近
一小时）。改为受控并发后，这里锁定几条不变量：

* 并发上限确实由 `analysis_concurrency` 约束（该配置此前从未生效）
* 单条失败不影响同批其他资讯
* 已有当前版本有效报告的资讯被跳过，不再重复烧钱
* 未评分资讯不会让整批崩掉（`agent_for_score(None)` 会抛 ValueError）
* 共享预取的市场快照与逐条自查结果逐字节一致（等价优化不改变结果）

测试手法沿用项目既有风格：**注入式假实现**（`analyzer=` 参数、monkeypatch
`session_scope` / `EventBus`），不连数据库、不调模型。
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date

import pytest

from fin_news.core.config import Settings
from fin_news.core.enums import AgentType, ReportStatus
from fin_news.domain.scoring import agent_for_score
from fin_news.pipeline.handlers import on_embedded


# ----------------------------------------------------------------------
# 假对象
# ----------------------------------------------------------------------
class _Res:
    """假查询结果：同时支持 `.scalars().all()` 与 `.scalar_one_or_none()`。"""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """按预设队列依次返回结果的假 session（handler 的查询顺序是确定的）。"""

    def __init__(self, results):
        self._q = list(results)
        self.commits = 0

    async def execute(self, stmt, *args, **kwargs):
        return _Res(self._q.pop(0) if self._q else [])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


class _FakeBus:
    """记录事件确认动作的假 EventBus。"""

    def __init__(self):
        self.acked: list[int] = []
        self.failed: list[int] = []
        self.released: list[int] = []
        self.published: list[int] = []

    @property
    def worker_id(self):
        return "test-worker"

    @staticmethod
    def _id(event):
        return event if isinstance(event, int) else event.id

    async def ack(self, event):
        self.acked.append(self._id(event))

    async def fail(self, event, error, error_type="HandlerError"):
        self.failed.append(self._id(event))

    async def release(self, events):
        if isinstance(events, int) or not isinstance(events, (list, tuple)):
            events = [events]
        self.released.extend(self._id(e) for e in events)

    async def publish(self, event_type, aggregate_id, payload=None, priority=None, **kw):
        self.published.append(aggregate_id)
        return 1


class _News:
    """只带 handler 所需字段的假资讯（避开 ORM 的必填字段）。"""

    def __init__(self, news_id: int, score: int | None):
        self.id = news_id
        self.score = score


class _Report:
    def __init__(self, report_id: int, status=ReportStatus.PUBLISHED):
        self.id = report_id
        self.status = status


class _Event:
    def __init__(self, event_id: int, news_id: int):
        self.id = event_id
        self.aggregate_id = news_id


def _settings(**kw) -> Settings:
    base = dict(
        volcengine_api_key="vk",
        deepseek_api_key="dk",
        llm_default_provider="volcengine",
        llm_fallback_provider="deepseek",
        analysis_concurrency=2,
        analysis_skip_existing=True,
    )
    base.update(kw)
    return Settings(_env_file=None, **base)


def _outer_session(items, existing=(), trade_day=date(2026, 9, 1)):
    """构造 handler 外层 session：按 handler 的查询顺序预置结果。

    顺序固定为：①NewsItem ②latest_trade_date ③market_snapshot ④已有报告查询。
    """
    return _FakeSession([list(items), [trade_day], [], list(existing)])


def _patch_scope(monkeypatch, sessions: list):
    """把 session_scope 换成返回独立假 session 的版本（模拟每任务独立事务）。"""

    @asynccontextmanager
    async def _fake_scope():
        s = _FakeSession([])
        sessions.append(s)
        yield s

    monkeypatch.setattr(on_embedded, "session_scope", _fake_scope)


def _spy_bus(monkeypatch, registry: list):
    """把任务内构造的 EventBus 换成可观测的假实现。"""

    class _Spy(_FakeBus):
        def __init__(self, session, worker_id="w"):
            super().__init__()
            registry.append(self)

    monkeypatch.setattr(on_embedded, "EventBus", _Spy)


# ----------------------------------------------------------------------
# 并发上限
# ----------------------------------------------------------------------
async def test_concurrency_is_capped_by_analysis_concurrency(monkeypatch):
    """同批在飞任务数不得超过 analysis_concurrency。"""
    settings = _settings(analysis_concurrency=2)
    items = [_News(1, 6), _News(2, 6), _News(3, 6), _News(4, 6), _News(5, 6)]
    events = [_Event(100 + n.id, n.id) for n in items]

    in_flight = 0
    peak = 0
    guard = asyncio.Lock()

    async def analyzer(session, news_id, settings, market_json=None):
        nonlocal in_flight, peak
        async with guard:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.03)
        async with guard:
            in_flight -= 1
        return _Report(news_id * 10)

    sessions: list = []
    buses: list = []
    _patch_scope(monkeypatch, sessions)
    _spy_bus(monkeypatch, buses)

    bus = _FakeBus()
    await on_embedded.handle(
        _outer_session(items), events, bus, settings, analyzer=analyzer
    )

    assert peak <= 2, f"并发上限失效，峰值 {peak} > analysis_concurrency=2"
    assert peak >= 2, f"没有真正并发起来，峰值仅 {peak}"
    # 每个并发任务都应持有自己的 session（AsyncSession 非并发安全，不能共用）
    assert len(sessions) == len(items)
    assert len({id(s) for s in sessions}) == len(items), "并发任务共用了 session"


# ----------------------------------------------------------------------
# 失败隔离
# ----------------------------------------------------------------------
async def test_single_failure_does_not_affect_others(monkeypatch):
    """某条抛异常时，其他资讯照常完成，且只有失败那条走 fail。"""
    settings = _settings(analysis_concurrency=3)
    items = [_News(1, 6), _News(2, 6), _News(3, 6)]
    events = [_Event(100 + n.id, n.id) for n in items]

    async def analyzer(session, news_id, settings, market_json=None):
        if news_id == 2:
            raise RuntimeError("模型调用炸了")
        return _Report(news_id * 10)

    sessions: list = []
    buses: list = []
    _patch_scope(monkeypatch, sessions)
    _spy_bus(monkeypatch, buses)

    bus = _FakeBus()
    # 不应向外抛出：单条失败必须被隔离在任务内
    await on_embedded.handle(
        _outer_session(items), events, bus, settings, analyzer=analyzer
    )

    acked = [i for b in buses for i in b.acked]
    failed = [i for b in buses for i in b.failed]
    assert failed == [102], f"只有失败那条应走 fail，实际 {failed}"
    assert sorted(acked) == [101, 103], f"其余应正常确认，实际 {sorted(acked)}"


# ----------------------------------------------------------------------
# 去重跳过
# ----------------------------------------------------------------------
async def test_existing_report_is_skipped_without_calling_llm(monkeypatch):
    """已有当前版本有效报告的资讯必须跳过，不再重复分析。"""
    settings = _settings(analysis_skip_existing=True)
    items = [_News(1, 6), _News(2, 6)]
    events = [_Event(101, 1), _Event(102, 2)]

    # 资讯 1 已有 industry.v2 的有效报告 → 应跳过；资讯 2 没有 → 应分析
    existing = [(1, AgentType.INDUSTRY, "industry.v2", ReportStatus.PUBLISHED)]

    called: list[int] = []

    async def analyzer(session, news_id, settings, market_json=None):
        called.append(news_id)
        return _Report(news_id * 10)

    sessions: list = []
    buses: list = []
    _patch_scope(monkeypatch, sessions)
    _spy_bus(monkeypatch, buses)

    bus = _FakeBus()
    await on_embedded.handle(
        _outer_session(items, existing=existing), events, bus, settings, analyzer=analyzer
    )

    assert called == [2], f"已有报告的资讯 1 不应再分析，实际调用了 {called}"
    assert 101 in bus.acked, "跳过的事件也要确认，否则会一直重试"


async def test_skip_existing_can_be_disabled(monkeypatch):
    """关闭开关后，已有报告的资讯也会重新分析（用于改 prompt 后强制重跑）。"""
    settings = _settings(analysis_skip_existing=False)
    items = [_News(1, 6)]
    events = [_Event(101, 1)]
    existing = [(1, AgentType.INDUSTRY, "industry.v2", ReportStatus.PUBLISHED)]

    called: list[int] = []

    async def analyzer(session, news_id, settings, market_json=None):
        called.append(news_id)
        return _Report(news_id * 10)

    sessions: list = []
    buses: list = []
    _patch_scope(monkeypatch, sessions)
    _spy_bus(monkeypatch, buses)

    await on_embedded.handle(
        _outer_session(items, existing=existing),
        events,
        _FakeBus(),
        settings,
        analyzer=analyzer,
    )
    assert called == [1]


# ----------------------------------------------------------------------
# 未评分防护
# ----------------------------------------------------------------------
def test_agent_for_score_rejects_none():
    """agent_for_score(None) 抛 ValueError —— 所以 handler 必须先拦掉。"""
    with pytest.raises(ValueError):
        agent_for_score(None)


async def test_unscored_news_is_acked_and_does_not_crash_batch(monkeypatch):
    """未评分资讯直接确认，不因 ValueError 炸掉整批。"""
    settings = _settings()
    items = [_News(1, None), _News(2, 6)]
    events = [_Event(101, 1), _Event(102, 2)]

    async def analyzer(session, news_id, settings, market_json=None):
        return _Report(news_id * 10)

    sessions: list = []
    buses: list = []
    _patch_scope(monkeypatch, sessions)
    _spy_bus(monkeypatch, buses)

    bus = _FakeBus()
    await on_embedded.handle(
        _outer_session(items), events, bus, settings, analyzer=analyzer
    )

    assert 101 in bus.acked, "未评分资讯应被直接确认"
    assert [i for b in buses for i in b.acked] == [102]


# ----------------------------------------------------------------------
# 共享预取的等价性
# ----------------------------------------------------------------------
async def test_prefetch_market_matches_per_item_serialization():
    """共享预取必须与 _build_context 逐条自查产生逐字节相同的字符串。"""
    from fin_news.agents.tools.market_data import market_snapshot

    shared = await on_embedded._prefetch_market(_FakeSession([[date(2026, 9, 1)], []]))
    assert shared is not None

    market = await market_snapshot(_FakeSession([]), date(2026, 9, 1))
    assert shared == json.dumps(market, ensure_ascii=False)[:2000]


async def test_prefetch_failure_falls_back_to_none():
    """预取失败时返回 None，交由各条资讯自行兜底查询，不阻断批次。"""

    class _Boom:
        async def execute(self, *a, **kw):
            raise RuntimeError("行情库不可用")

    assert await on_embedded._prefetch_market(_Boom()) is None
