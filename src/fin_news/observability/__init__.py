"""Agent 运行可观测性：埋点写入（tracker）与指标查询（metrics）。

分两层是刻意的：

* `tracker` —— **写入侧**，在 Agent 执行入口落 `agent_run`，关注「不丢、不阻断」
* `metrics` —— **查询侧**，把指标口径收在一处，供 CLI / 未来的 Web 面板与 API
  复用，避免同一段聚合 SQL 散落到多个地方后各写各的、口径漂移
"""
from fin_news.observability.metrics import (
    AgentHealthRow,
    LLMSummaryRow,
    ReportQualityRow,
    agent_health,
    llm_summary,
    report_quality,
)
from fin_news.observability.tracker import AgentRunTracker, digest_of

__all__ = [
    "AgentHealthRow",
    "AgentRunTracker",
    "LLMSummaryRow",
    "ReportQualityRow",
    "agent_health",
    "digest_of",
    "llm_summary",
    "report_quality",
]
