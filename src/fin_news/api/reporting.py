"""报告查询的共享约定与 SQL 片段。

news / analysis / market 三个路由都要按同一口径筛选 AnalysisReport、都要用「分档优先级」
表达「重要程度」。集中定义，避免各处在 SQL 里各写一套枚举列表导致口径漂移。
"""
from __future__ import annotations

from sqlalchemy import ColumnElement, case, nullsfirst, nullslast

from fin_news.core.enums import AgentType, ReportStatus
from fin_news.domain.scoring import BAND_PRIORITY

# 资讯级深度分析 Agent：宏观政策 / 行业 / 个股（排除盘前盘后简报与追问）
NEWS_ANALYSIS_AGENTS = [AgentType.MACRO_POLICY, AgentType.INDUSTRY, AgentType.STOCK]

# 对外可见的报告状态：DRAFT 未发布、SUPERSEDED 已被新版取代
VISIBLE_REPORT_STATUS = [ReportStatus.PUBLISHED, ReportStatus.DEGRADED]


def band_rank(band_column) -> ColumnElement[int]:
    """分档 -> 重要程度权重：MACRO 3 > INDUSTRY 2 > STOCK 1 > NOISE 0。

    复用 domain.scoring.BAND_PRIORITY，避免两处维护同一套权重。
    """
    return case(
        *[(band_column == band, priority) for band, priority in BAND_PRIORITY.items()],
        else_=0,
    )


def order_by_rank(keys: list[ColumnElement], order: str) -> list[ColumnElement]:
    """把排序键按方向包一层，空值统一排末尾 / 开头，保证结果顺序可预期。"""
    if order == "asc":
        return [nullsfirst(key.asc()) for key in keys]
    return [nullslast(key.desc()) for key in keys]
