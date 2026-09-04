"""给 DeepAgents 用的 LangChain 工具（每个工具自带会话，避免跨事务污染）。

容错约定（重要）：
ReAct 循环里，工具抛出的异常会被 LangGraph 的 tool_node 向上传播、终止整张图，
Agent 连「看到错误 → 调整参数重试」的机会都没有。因此所有工具统一由
`_tool_error_guard` 包装：任何异常都转成可读的错误字符串返回，成为 ReAct 循环里
一次「可观察的失败」，由 Agent 自行纠正。

实测触发场景：模型给 market_snapshot 传了 Tushare 风格的紧凑日期 '20260901'，
strptime 直接抛 ValueError，整张图（含子 agent）崩溃。
"""
from __future__ import annotations

import functools
import json
from datetime import date, datetime

from langchain_core.tools import tool

from fin_news.core.config import get_settings
from fin_news.core.db import session_scope
from fin_news.core.logging import get_logger

logger = get_logger("agents.tools.langchain")

# 模型常见的日期写法：Tushare 接口用紧凑格式，模型容易与 ISO 格式混用
_DATE_FORMATS = ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d")


def _tool_error_guard(func):
    """把工具异常转成可观察的错误字符串，避免炸掉整张图。

    与 web_search 既有的「返回不可用提示」保持一致的语义：工具失败应当是
    ReAct 循环里的一次观察结果，而不是终止信号。
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        name = getattr(func, "__name__", "tool")
        try:
            return await func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 工具失败需可恢复
            logger.warning("工具调用失败", tool=name, error=str(exc)[:200])
            return f"（{name} 调用失败：{str(exc)[:200]}，请调整参数后重试）"

    return wrapper


def _parse_trade_date(value: str) -> date | None:
    """解析交易日参数，兼容多种写法；无法识别时抛出说明性错误（由 guard 兜住）。"""
    text = (value or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"无法识别的日期：{text!r}，请用 YYYY-MM-DD（如 2026-09-01）")


@tool
@_tool_error_guard
async def history_search(query: str, top_k: int = 8) -> str:
    """检索历史相似资讯。输入自然语言查询（如「央行降准后券商板块表现」），返回相关历史新闻片段。"""
    from fin_news.agents.tools.retrieval import format_hits
    from fin_news.agents.tools.retrieval import history_search as _search

    async with session_scope() as session:
        hits = await _search(session, query, top_k=min(max(1, top_k), 20))
        return format_hits(hits)


@tool
@_tool_error_guard
async def web_search(query: str, max_results: int = 5) -> str:
    """检索外部公开信息（新闻、机构解读、海外反应）。仅在需要补充背景时使用；未配置时返回不可用提示。"""
    from fin_news.agents.tools.web_search import WebSearchUnavailable, format_results
    from fin_news.agents.tools.web_search import web_search as _ws

    try:
        results = await _ws(query, max_results=max_results)
    except WebSearchUnavailable as exc:
        return f"（外部检索不可用：{exc}）"
    except Exception as exc:  # noqa: BLE001
        return f"（外部检索失败：{str(exc)[:200]}）"
    return format_results(results)


@tool
@_tool_error_guard
async def stock_lookup(ts_code: str) -> str:
    """查询个股的估值与近期走势。参数为 Tushare 代码，如 600519.SH / 000001.SZ / 300308.SZ。"""
    from fin_news.agents.tools.market_data import stock_snapshot

    async with session_scope() as session:
        data = await stock_snapshot(session, ts_code)
    return json.dumps(data, ensure_ascii=False)


@tool
@_tool_error_guard
async def market_snapshot(trade_date: str = "") -> str:
    """查询指定交易日的市场快照（指数、涨跌家数、成交额、板块）。

    trade_date 用 YYYY-MM-DD（如 2026-09-01）；留空表示最近一个交易日。
    """
    from fin_news.agents.tools.market_data import latest_trade_date
    from fin_news.agents.tools.market_data import market_snapshot as _snapshot

    day = _parse_trade_date(trade_date)
    async with session_scope() as session:
        if day is None:
            day = await latest_trade_date(session) or date.today()
        data = await _snapshot(session, day)
    return json.dumps(data, ensure_ascii=False)


@tool
@_tool_error_guard
async def article_search(query: str, top_k: int = 8) -> str:
    """检索我（本公众号）历史已发布的文章片段。输入自然语言查询，返回相关历史文章标题与片段。

    写新文章前用它回顾「我之前写过什么」，可引用「我之前的文章里讲过 xx」，避免重复讲解。
    只能检索到已发布（PUBLISHED）的历史文章。
    """
    from fin_news.agents.tools.article_retrieval import article_search as _search
    from fin_news.agents.tools.article_retrieval import format_article_hits

    async with session_scope() as session:
        hits = await _search(session, query, top_k=min(max(1, top_k), 20))
        return format_article_hits(hits)


def build_toolset(agent_type: str) -> list:
    """按 Agent 类型装配工具（控制成本：只有宏观 Agent 默认开放联网检索）。"""
    settings = get_settings()
    tools: list = [history_search, stock_lookup, market_snapshot]
    if agent_type == "macro_policy" and settings.web_search_enabled:
        tools.insert(1, web_search)
    return tools
