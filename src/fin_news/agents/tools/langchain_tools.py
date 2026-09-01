"""给 DeepAgents 用的 LangChain 工具（每个工具自带会话，避免跨事务污染）。"""
from __future__ import annotations

import json

from langchain_core.tools import tool

from fin_news.core.config import get_settings
from fin_news.core.db import session_scope
from fin_news.core.logging import get_logger

logger = get_logger("agents.tools.langchain")


@tool
async def history_search(query: str, top_k: int = 8) -> str:
    """检索历史相似资讯。输入自然语言查询（如「央行降准后券商板块表现」），返回相关历史新闻片段。"""
    from fin_news.agents.tools.retrieval import format_hits
    from fin_news.agents.tools.retrieval import history_search as _search

    async with session_scope() as session:
        hits = await _search(session, query, top_k=min(max(1, top_k), 20))
        return format_hits(hits)


@tool
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
async def stock_lookup(ts_code: str) -> str:
    """查询个股的估值与近期走势。参数为 Tushare 代码，如 600519.SH / 000001.SZ / 300308.SZ。"""
    from fin_news.agents.tools.market_data import stock_snapshot

    async with session_scope() as session:
        data = await stock_snapshot(session, ts_code)
    return json.dumps(data, ensure_ascii=False)


@tool
async def market_snapshot(trade_date: str = "") -> str:
    """查询指定交易日的市场快照（指数、涨跌家数、成交额、板块）。trade_date 为空表示最近一个交易日。"""
    from datetime import date, datetime

    from fin_news.agents.tools.market_data import latest_trade_date
    from fin_news.agents.tools.market_data import market_snapshot as _snapshot

    async with session_scope() as session:
        day: date | None
        if trade_date:
            day = datetime.strptime(trade_date, "%Y-%m-%d").date()
        else:
            day = await latest_trade_date(session) or date.today()
        data = await _snapshot(session, day)
    return json.dumps(data, ensure_ascii=False)


def build_toolset(agent_type: str) -> list:
    """按 Agent 类型装配工具（控制成本：只有宏观 Agent 默认开放联网检索）。"""
    settings = get_settings()
    tools: list = [history_search, stock_lookup, market_snapshot]
    if agent_type == "macro_policy" and settings.web_search_enabled:
        tools.insert(1, web_search)
    return tools
