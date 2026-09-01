"""评分 Agent 的 LangGraph 图。

流程：

    call_model → validate →（有漏评且补打轮次未用完）→ rescue → call_model → …
                          └─（否则）→ END

相比原实现的好处：
* 结构化输出是原生的（Pydantic + provider schema），不再靠正则从文本里捞 JSON
* 漏评补打是显式的图节点，而不是散落在调用方的一坨 for 循环
* 图按 (agent, prompt_version) 缓存，编译一次复用多次
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from fin_news.agents.llm.factory import get_model_factory
from fin_news.agents.prompts import SCORING_SYSTEM, SCORING_VERSION
from fin_news.agents.schemas import ScoreBatchModel, ScoreItemModel
from fin_news.core.config import Settings, get_settings
from fin_news.core.logging import get_logger
from fin_news.domain.scoring import clamp_score
from fin_news.domain.textutil import truncate
from fin_news.models.news import NewsItem

logger = get_logger("agents.graphs.scoring")

# 漏评补打的最大轮数（每轮会重新调用一次模型）
MAX_RESCUE_ROUNDS = 2


class ScoringState(TypedDict, total=False):
    pending: list[NewsItem]  # 本轮待评分（列表顺序即编号 1..N）
    payload: str  # 本轮用户提示
    raw: ScoreBatchModel | None  # 模型本轮输出
    scored: dict[int, ScoreItemModel]  # news_id -> 结果（跨轮累积）
    missing: list[NewsItem]  # 本轮结束后仍未评上的
    rounds: int  # 已补打轮次
    suspect: bool  # 批内分数过度集中
    structured_method: str  # 实际生效的结构化输出方式
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    error: str | None


@dataclass
class ScoringRun:
    """一次评分图的执行结果。"""

    items: dict[int, ScoreItemModel] = field(default_factory=dict)
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    is_suspect: bool = False
    rounds: int = 0
    structured_method: str = ""
    error: str | None = None


# ----------------------------------------------------------------------
# 提示构建（与 legacy 实现完全一致的编号规则，保证结果可比）
# ----------------------------------------------------------------------


def build_payload(items: list[NewsItem], max_chars: int) -> str:
    """构造批量评分提示：编号 1..N，编号与列表顺序一一对应。"""
    from fin_news.agents.prompts import SCORING_USER_TEMPLATE

    lines = []
    for idx, item in enumerate(items, start=1):
        content, _ = truncate(item.content or item.title or "", max_chars)
        time_str = item.publish_time.strftime("%Y-%m-%d %H:%M") if item.publish_time else "时间未知"
        lines.append(f"{idx}. 【{item.src_name or item.src}】{time_str} 标题：{item.title}\n   正文：{content}")
    return SCORING_USER_TEMPLATE.format(count=len(items), items="\n".join(lines))


# ----------------------------------------------------------------------
# 节点
# ----------------------------------------------------------------------


def _messages(state: ScoringState) -> list[Any]:
    return [SystemMessage(content=SCORING_SYSTEM), HumanMessage(content=state.get("payload") or "")]


def _usage_of(raw_message: Any) -> tuple[int, int]:
    meta = getattr(raw_message, "usage_metadata", None) or {}
    if meta:
        return int(meta.get("input_tokens") or 0), int(meta.get("output_tokens") or 0)
    rm = getattr(raw_message, "response_metadata", None) or {}
    for key in ("token_usage", "usage"):
        usage = rm.get(key) or {}
        if usage:
            return (
                int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
            )
    return 0, 0


def make_call_model(settings: Settings, *, chat: Any | None = None):
    """构建 call_model 节点。

    chat 可注入，便于测试；默认按 json_schema → function_calling 顺序在**调用时**降级
    （有些模型的绑定阶段不报错，真正调用才失败）。
    """
    runnables: list[tuple[str, Any]] = []
    if chat is not None:
        for method in ("json_schema", "function_calling"):
            try:
                runnables.append((method, chat.with_structured_output(ScoreBatchModel, method=method, include_raw=True)))
            except Exception:  # noqa: BLE001
                continue
        if not runnables:
            runnables.append(("default", chat.with_structured_output(ScoreBatchModel, include_raw=True)))
    else:
        factory = get_model_factory(settings)
        for method in ("json_schema", "function_calling"):
            try:
                runnables.append(
                    (method, factory.structured("scoring", ScoreBatchModel, method=method, include_raw=True))
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("结构化输出方式不可用", method=method, error=str(exc)[:200])

    async def call_model(state: ScoringState) -> dict:
        started = time.perf_counter()
        last_error: str | None = None

        for method, runnable in runnables:
            try:
                out = await runnable.ainvoke(_messages(state))
            except Exception as exc:  # noqa: BLE001 - 换下一种方式继续
                last_error = f"{method}: {type(exc).__name__}: {str(exc)[:200]}"
                logger.warning("评分调用失败，尝试下一种结构化方式", method=method, error=str(exc)[:200])
                continue

            parsed = out.get("parsed") if isinstance(out, dict) else out
            if parsed is None:
                last_error = f"{method}: {out.get('parsing_error') or '解析失败'}"
                continue

            raw_message = out.get("raw") if isinstance(out, dict) else None
            prompt_tokens, completion_tokens = _usage_of(raw_message)
            meta = getattr(raw_message, "response_metadata", None) or {}
            model_name = str(meta.get("model_name") or meta.get("model") or "")
            scores = [i.score for i in parsed.items]
            logger.info(
                "评分模型返回",
                method=method,
                model=model_name,
                count=len(scores),
                scores=scores,
            )

            return {
                "structured_method": method,
                "raw": parsed,
                "model": model_name,
                "prompt_tokens": int(state.get("prompt_tokens") or 0) + prompt_tokens,
                "completion_tokens": int(state.get("completion_tokens") or 0) + completion_tokens,
                "latency_ms": int(state.get("latency_ms") or 0) + int((time.perf_counter() - started) * 1000),
                "error": None,
            }

        return {
            "raw": None,
            "latency_ms": int(state.get("latency_ms") or 0) + int((time.perf_counter() - started) * 1000),
            "error": last_error or "无可用结构化输出方式",
        }

    return call_model


def validate_node(state: ScoringState) -> dict:
    """校验模型输出：编号映射、去重、分数 clamp、漏评统计、分布异常检测。"""
    pending = list(state.get("pending") or [])
    raw = state.get("raw")
    scored: dict[int, ScoreItemModel] = dict(state.get("scored") or {})

    if raw is not None:
        seen: set[int] = set()
        for item in raw.items:
            idx = item.id
            # 编号越界 = 模型幻觉，直接丢弃
            if not isinstance(idx, int) or idx < 1 or idx > len(pending):
                continue
            news_id = pending[idx - 1].id
            if news_id in seen or news_id in scored:
                continue
            seen.add(news_id)
            scored[news_id] = ScoreItemModel(
                id=news_id,
                score=clamp_score(item.score),
                reason=(item.reason or "")[:200],
                tags=list(item.tags or []),
                entities=list(item.entities or []),
                confidence=item.confidence,
            )

    missing = [n for n in pending if n.id not in scored]

    suspect = False
    if len(scored) >= 5:
        top = max((s.score for s in scored.values()), default=0)
        suspect = sum(1 for s in scored.values() if s.score == top) / len(scored) >= 0.8

    return {"scored": scored, "missing": missing, "suspect": suspect}


def rescue_node(state: ScoringState) -> dict:
    """把漏评的条目重新编号成新的一批，进入下一轮补打。"""
    settings = get_settings()
    missing = list(state.get("missing") or [])
    return {
        "pending": missing,
        "payload": build_payload(missing, settings.scoring_max_content_chars),
        "raw": None,
        "rounds": int(state.get("rounds") or 0) + 1,
    }


def _is_degenerate(run: ScoringRun, expected: int) -> bool:
    """判定评分结果是否退化。

    实测出现过整批 20 条只给 1-2 分的情况（模型"偷懒"），
    这类结果会直接把资讯全部判成噪声，必须拦住。
    """
    if expected <= 0 or not run.items:
        return True
    if len(run.items) / expected < 0.8:  # 覆盖率过低
        return True
    if expected >= 5 and len({s.score for s in run.items.values()}) <= 2:
        return True
    return run.is_suspect


def _quality(run: ScoringRun) -> tuple[int, int]:
    """结果质量：(已评条数, 分数种类数)，用于重试后择优。"""
    return len(run.items), len({s.score for s in run.items.values()})


def _route(state: ScoringState) -> str:
    if state.get("missing") and int(state.get("rounds") or 0) < MAX_RESCUE_ROUNDS:
        return "rescue"
    return "end"


# ----------------------------------------------------------------------
# 图构建与执行
# ----------------------------------------------------------------------


def build_scoring_graph(settings: Settings | None = None, *, chat: Any | None = None):
    settings = settings or get_settings()
    graph = StateGraph(ScoringState)
    graph.add_node("call_model", make_call_model(settings, chat=chat))
    graph.add_node("validate", validate_node)
    graph.add_node("rescue", rescue_node)
    graph.set_entry_point("call_model")
    graph.add_edge("call_model", "validate")
    graph.add_conditional_edges("validate", _route, {"rescue": "rescue", "end": END})
    graph.add_edge("rescue", "call_model")
    return graph.compile()


_graphs: dict[str, Any] = {}


def get_scoring_graph(settings: Settings) -> Any:
    """按 (prompt_version, provider, model) 缓存已编译的图。"""
    key = "|".join(
        [
            SCORING_VERSION,
            settings.llm_default_provider,
            settings.model_for(settings.llm_default_provider, "scoring"),
        ]
    )
    if key not in _graphs:
        _graphs[key] = build_scoring_graph(settings)
        logger.info("构建评分图", key=key)
    return _graphs[key]


async def run_scoring(
    items: list[NewsItem], settings: Settings | None = None, *, chat: Any | None = None
) -> ScoringRun:
    """执行一次评分图，返回结构化结果。"""
    settings = settings or get_settings()
    if chat is not None:
        graph = build_scoring_graph(settings, chat=chat)  # 注入模型时不走缓存
    else:
        graph = get_scoring_graph(settings)

    state: ScoringState = {
        "pending": list(items),
        "payload": build_payload(list(items), settings.scoring_max_content_chars),
        "raw": None,
        "scored": {},
        "missing": [],
        "rounds": 0,
        "suspect": False,
        "model": "",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "latency_ms": 0,
        "error": None,
    }

    timeout = settings.llm_timeout_seconds * (1 + MAX_RESCUE_ROUNDS)
    out = await asyncio.wait_for(graph.ainvoke(state), timeout=timeout)
    run = _to_run(out)

    # 退化护栏：模型偶尔会整批给同一个分，重试一次并择优
    if settings.score_retry_on_degenerate and _is_degenerate(run, len(items)):
        logger.warning(
            "评分结果疑似退化，重试一次",
            expected=len(items),
            scored=len(run.items),
            distinct=len({s.score for s in run.items.values()}),
        )
        retry = _to_run(await asyncio.wait_for(graph.ainvoke(state), timeout=timeout))
        if _quality(retry) > _quality(run):
            logger.info(
                "重试结果更优，采用重试结果",
                before=_quality(run),
                after=_quality(retry),
            )
            run = retry
        else:
            logger.warning("重试未改善，保留原结果", before=_quality(run), after=_quality(retry))

    return run


def _to_run(out: dict) -> ScoringRun:
    return ScoringRun(
        items=dict(out.get("scored") or {}),
        model=out.get("model") or "",
        structured_method=str(out.get("structured_method") or ""),
        prompt_tokens=int(out.get("prompt_tokens") or 0),
        completion_tokens=int(out.get("completion_tokens") or 0),
        latency_ms=int(out.get("latency_ms") or 0),
        is_suspect=bool(out.get("suspect")),
        rounds=int(out.get("rounds") or 0),
        error=out.get("error"),
    )
