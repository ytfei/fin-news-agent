"""LangChain 工具的日期解析与容错单测（不发起真实模型请求）。

背景：模型给 market_snapshot 传了 Tushare 风格的紧凑日期 '20260901'，
strptime 抛 ValueError 被 LangGraph tool_node 向上传播，直接炸掉整张图
（含子 agent）。这两类防护必须有测试兜底。
"""
from __future__ import annotations

from datetime import date

import pytest

from fin_news.agents.tools.langchain_tools import (
    _parse_trade_date,
    history_search,
    market_snapshot,
    stock_lookup,
    web_search,
)

# ------------------------------ 日期解析 ------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-01", date(2026, 9, 1)),  # ISO 标准
        ("20260901", date(2026, 9, 1)),  # Tushare 紧凑格式（模型常用，曾致崩溃）
        ("2026/09/01", date(2026, 9, 1)),  # 斜杠分隔
        ("  2026-09-01  ", date(2026, 9, 1)),  # 带首尾空白
    ],
)
def test_parse_trade_date_accepts_common_formats(raw: str, expected: date):
    assert _parse_trade_date(raw) == expected


def test_parse_trade_date_empty_means_latest():
    """留空表示「最近一个交易日」，由调用方回落到 latest_trade_date。"""
    assert _parse_trade_date("") is None
    assert _parse_trade_date("   ") is None


def test_parse_trade_date_rejects_garbage():
    with pytest.raises(ValueError, match="无法识别的日期"):
        _parse_trade_date("garbage")


# ------------------------------ 工具容错 ------------------------------


async def test_market_snapshot_bad_date_returns_message_not_raise():
    """非法日期必须返回可观察的错误字符串，而不是抛异常终止整张图。"""
    out = await market_snapshot.ainvoke({"trade_date": "garbage"})
    assert isinstance(out, str)
    assert "调用失败" in out
    assert "garbage" in out


async def test_tools_keep_expected_args_after_guard():
    """@tool 与容错装饰器组合后，参数 schema 仍要能被模型正确填充。"""
    assert list(market_snapshot.args_schema.model_json_schema()["properties"]) == ["trade_date"]
    assert list(history_search.args_schema.model_json_schema()["properties"]) == ["query", "top_k"]
    assert list(stock_lookup.args_schema.model_json_schema()["properties"]) == ["ts_code"]
    assert list(web_search.args_schema.model_json_schema()["properties"]) == ["query", "max_results"]
