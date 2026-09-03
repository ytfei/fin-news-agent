"""评分小批并发：子批切分、信号量并发上限、结果合并与 suspect 兜底。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fin_news.agents.prompts import SCORING_VERSION
from fin_news.agents.scoring_agent import ScoringAgent
from fin_news.core.config import Settings
from fin_news.domain.schemas import ScoreBatchResult, ScoreItemResult


def _settings(**kw) -> Settings:
    base = dict(
        volcengine_api_key="vk",
        deepseek_api_key="dk",
        llm_default_provider="volcengine",
        scoring_sub_batch_size=10,
        scoring_concurrency=4,
        score_dual_run=False,
        agent_framework="langgraph",
    )
    base.update(kw)
    return Settings(_env_file=None, **base)  # pyright: ignore[reportCallIssue]


def _items(n: int) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(id=i, status="NEW", retry_count=0, score=None, last_error=None)
        for i in range(n)
    ]


def _item_result(nid: int) -> ScoreItemResult:
    return ScoreItemResult(id=nid, score=7, reason="r", tags=[], entities=[], confidence=0.6)


def _batch_result(nids: list[int], **kw) -> ScoreBatchResult:
    base = dict(
        model="fake",
        prompt_version=SCORING_VERSION,
        latency_ms=5,
        prompt_tokens=10,
        completion_tokens=2,
        is_suspect=False,
    )
    base.update(kw)
    return ScoreBatchResult(items=[_item_result(n) for n in nids], **base)


def _patch_score_items(monkeypatch, agent: ScoringAgent) -> dict:
    """子批执行换成可控 fake（带 sleep 观测并发），其余 IO 全部打桩。"""
    state = {"calls": 0, "inflight": 0, "peak": 0}

    async def fake_sub(items: list[SimpleNamespace]):
        state["calls"] += 1
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        await asyncio.sleep(0.02)
        state["inflight"] -= 1
        return _batch_result([i.id for i in items])

    monkeypatch.setattr(agent, "_score_sub_batch", fake_sub)
    monkeypatch.setattr(agent, "_persist", AsyncMock())
    monkeypatch.setattr(agent, "_publish_scored_events", AsyncMock(return_value=0))
    return state


# ----------------------------------------------------------------------
# 切分
# ----------------------------------------------------------------------


def test_split_subs_single_batch_within_limit():
    agent = ScoringAgent(_settings())
    subs = agent._split_subs(_items(8))
    assert [len(s) for s in subs] == [8]


def test_split_subs_slices_into_subbatches_preserving_order():
    agent = ScoringAgent(_settings(scoring_sub_batch_size=10))
    subs = agent._split_subs(_items(25))
    assert [len(s) for s in subs] == [10, 10, 5]
    # 合并依赖原始顺序：切分后重新展平必须与输入一致
    ids = [s.id for sub in subs for s in sub]
    assert ids == list(range(25))


# ----------------------------------------------------------------------
# 并发执行
# ----------------------------------------------------------------------


async def test_score_items_runs_all_subbatches_in_parallel(monkeypatch):
    """25 条 -> 3 个子批全部并行（scoring_concurrency=4 足够）。"""
    agent = ScoringAgent(_settings())
    state = _patch_score_items(monkeypatch, agent)
    out = await agent.score_items(None, _items(25))
    assert state["calls"] == 3
    assert state["peak"] == 3
    assert len(out) == 25
    assert sorted(out.keys()) == list(range(25))


async def test_score_items_respects_scoring_concurrency_limit(monkeypatch):
    """并发子批数不超过 scoring_concurrency：5 个子批只允许 2 个 in-flight。"""
    agent = ScoringAgent(_settings(scoring_sub_batch_size=2, scoring_concurrency=2))
    # limiter._semaphores 按 role 进程级缓存，首个创建者决定上限；
    # 本用例注入与测试 settings 一致的信号量，验证 score_items 自身限流逻辑。
    monkeypatch.setattr(
        "fin_news.agents.scoring_agent.get_semaphore",
        lambda _role, settings=None: asyncio.Semaphore(
            max(1, (settings or agent.settings).scoring_concurrency)
        ),
    )
    state = _patch_score_items(monkeypatch, agent)
    await agent.score_items(None, _items(10))
    assert state["calls"] == 5
    assert state["peak"] == 2


# ----------------------------------------------------------------------
# 结果合并
# ----------------------------------------------------------------------


def test_merge_preserves_order_and_aggregates():
    agent = ScoringAgent(_settings())
    items = _items(6)
    sub_a = _batch_result([0, 1, 2], model="m1", latency_ms=10, prompt_tokens=10, completion_tokens=2)
    sub_b = _batch_result([3, 4, 5], model="m2", latency_ms=30, prompt_tokens=20, completion_tokens=4)
    merged = agent._merge_sub_results(items, [items[:3], items[3:]], [sub_a, sub_b])
    assert [r.id for r in merged.items] == [0, 1, 2, 3, 4, 5]  # 原始顺序
    assert merged.latency_ms == 30  # 取子批最大值
    assert merged.prompt_tokens == 30  # 累计
    assert merged.completion_tokens == 6
    assert merged.model == "m1"  # 首个非空


def test_merge_suspect_or_across_subs():
    """任一子批 suspect 或整批同分集中（跨子批兜底）都标 suspect。"""
    agent = ScoringAgent(_settings())
    items = _items(6)
    # 每个子批 3 条不触发单批阈值；整批 6 条全 10 分触发兜底
    sub_a = _batch_result([0, 1, 2], is_suspect=False)
    sub_b = _batch_result([3, 4, 5], is_suspect=False)
    merged = agent._merge_sub_results(items, [items[:3], items[3:]], [sub_a, sub_b])
    assert merged.is_suspect is True

    # 单子批 suspect 也会向上传递
    sub_a2 = _batch_result([0, 1, 2], is_suspect=True)
    merged2 = agent._merge_sub_results(items, [items[:3], items[3:]], [sub_a2, sub_b])
    assert merged2.is_suspect is True


def test_merge_ignores_exception_subbatch():
    """异常子批按未评分处理，不拖垮整批合并。"""
    agent = ScoringAgent(_settings())
    items = _items(6)
    good = _batch_result([0, 1, 2])
    merged = agent._merge_sub_results(items, [items[:3], items[3:]], [good, RuntimeError("boom")])
    assert [r.id for r in merged.items] == [0, 1, 2]
    assert merged.latency_ms == good.latency_ms
