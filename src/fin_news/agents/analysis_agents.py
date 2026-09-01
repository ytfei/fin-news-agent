"""深度分析 Agent：按评分路由到宏观政策 / 行业 / 个股 Agent，产出结构化报告。"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.base import AgentOutput, run_agent
from fin_news.agents.prompts import (
    INDUSTRY_SYSTEM,
    INDUSTRY_USER_TEMPLATE,
    INDUSTRY_VERSION,
    MACRO_SYSTEM,
    MACRO_USER_TEMPLATE,
    MACRO_VERSION,
    STOCK_SYSTEM,
    STOCK_USER_TEMPLATE,
    STOCK_VERSION,
)
from fin_news.agents.tools.langchain_tools import build_toolset
from fin_news.agents.tools.market_data import latest_trade_date, market_snapshot
from fin_news.agents.tools.retrieval import format_hits, history_search
from fin_news.agents.tools.web_search import WebSearchUnavailable, format_results, web_search
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import AgentType, NewsStatus, ReportStatus
from fin_news.core.logging import get_logger
from fin_news.core.timeutil import now_utc
from fin_news.domain.scoring import agent_for_score
from fin_news.domain.textutil import truncate
from fin_news.models.analysis import AnalysisReport
from fin_news.models.news import NewsItem

logger = get_logger("agents.analysis")

AGENT_CONFIG: dict[AgentType, tuple[str, str, str]] = {
    # agent_type -> (system_prompt, user_template, version)
    AgentType.MACRO_POLICY: (MACRO_SYSTEM, MACRO_USER_TEMPLATE, MACRO_VERSION),
    AgentType.INDUSTRY: (INDUSTRY_SYSTEM, INDUSTRY_USER_TEMPLATE, INDUSTRY_VERSION),
    AgentType.STOCK: (STOCK_SYSTEM, STOCK_USER_TEMPLATE, STOCK_VERSION),
}

MAX_CONTENT_CHARS = 3000
MAX_HISTORY_CHARS = 2500


async def analyze_news(
    session: AsyncSession, news: NewsItem, settings: Settings | None = None
) -> AnalysisReport | None:
    """对单条资讯执行深度分析，并落库。"""
    settings = settings or get_settings()
    agent_type = agent_for_score(news.score)
    if agent_type is None:
        return None

    system_prompt, user_template, version = AGENT_CONFIG[agent_type]
    news.status = NewsStatus.ANALYZING
    news.analysis_status = "PENDING"
    await session.flush()

    context = await _build_context(session, news, agent_type, settings)
    user_prompt = user_template.format(**context)
    tools = build_toolset(agent_type.value)

    output: AgentOutput = await run_agent(
        agent_type,
        system_prompt,
        user_prompt,
        tools=tools,
        settings=settings,
    )

    report = await _persist(session, news, agent_type, version, output, context)
    news.status = NewsStatus.ANALYZED
    news.analysis_status = "DONE"
    news.retry_count = 0
    news.last_error = None
    await session.flush()

    logger.info(
        "分析完成",
        agent=agent_type.value,
        news_id=news.id,
        report_id=report.id,
        degraded=output.degraded,
        latency_ms=output.latency_ms,
    )
    return report


async def analyze_news_by_id(
    session: AsyncSession, news_id: int, settings: Settings | None = None
) -> AnalysisReport | None:
    news = (
        await session.execute(select(NewsItem).where(NewsItem.id == news_id))
    ).scalar_one_or_none()
    if news is None:
        return None
    return await analyze_news(session, news, settings)


# ----------------------------------------------------------------------
async def _build_context(
    session: AsyncSession, news: NewsItem, agent_type: AgentType, settings: Settings
) -> dict:
    title = news.title or ""
    content, _ = truncate(news.content or title, MAX_CONTENT_CHARS)
    query = f"{title}\n{(news.content or '')[:300]}"

    # 1) 历史相似资讯（向量检索）
    try:
        hits = await history_search(session, query, top_k=6, exclude_news_id=news.id)
        history_text = truncate(format_hits(hits), MAX_HISTORY_CHARS)[0]
        ref_ids = [h.news_id for h in hits]
    except Exception as exc:  # noqa: BLE001 - 检索失败不阻塞分析
        logger.warning("历史检索失败", news_id=news.id, error=str(exc)[:200])
        history_text, ref_ids = "（历史检索不可用）", []

    # 2) 外部信息检索（仅宏观 Agent 强制）
    external_text = "（本次未启用外部检索）"
    external_sources: list[dict] = []
    if agent_type == AgentType.MACRO_POLICY and settings.web_search_enabled:
        try:
            results = await web_search(f"{title} 市场影响 解读", max_results=5, settings=settings)
            external_text = truncate(format_results(results), MAX_HISTORY_CHARS)[0]
            external_sources = results
        except WebSearchUnavailable as exc:
            external_text = f"（外部检索不可用：{exc}）"
        except Exception as exc:  # noqa: BLE001
            external_text = f"（外部检索失败：{str(exc)[:200]}）"

    # 3) 市场快照
    try:
        trade_date = await latest_trade_date(session) or date.today()
        market = await market_snapshot(session, trade_date)
    except Exception as exc:  # noqa: BLE001
        logger.warning("市场快照获取失败", error=str(exc)[:200])
        market = {}

    return {
        "title": title,
        "src_name": news.src_name or news.src or "未知来源",
        "publish_time": news.publish_time.strftime("%Y-%m-%d %H:%M") if news.publish_time else "未知",
        "score": news.score,
        "band": news.band.value if news.band else "",
        "content": content,
        "history": history_text,
        "external": external_text,
        "market": json.dumps(market, ensure_ascii=False)[:2000],
        "_ref_ids": ref_ids,
        "_external_sources": external_sources,
    }


async def _persist(
    session: AsyncSession,
    news: NewsItem,
    agent_type: AgentType,
    version: str,
    output: AgentOutput,
    context: dict,
) -> AnalysisReport:
    """写入报告；同一 (news_id, agent_type, prompt_version) 只保留一份生效报告。"""
    await session.execute(
        update(AnalysisReport)
        .where(
            AnalysisReport.news_id == news.id,
            AnalysisReport.agent_type == agent_type,
            AnalysisReport.prompt_version == version,
            AnalysisReport.status.in_(
                [ReportStatus.DRAFT, ReportStatus.PUBLISHED, ReportStatus.DEGRADED]
            ),
        )
        .values(status=ReportStatus.SUPERSEDED)
    )

    data = output.data or {}
    report = AnalysisReport(
        agent_type=agent_type,
        news_id=news.id,
        title=str(data.get("headline") or news.title or "")[:255] or news.title[:255],
        summary=str(data.get("summary") or data.get("headline") or "")[:2000],
        content=data,
        score=news.score,
        band=news.band,
        sentiment=data.get("sentiment") or "neutral",
        impact_level=data.get("impact_level") or "medium",
        horizon=data.get("horizon") or "short",
        confidence=float(data.get("confidence", 0.6) or 0.6),
        beneficiaries=data.get("beneficiaries") or [],
        victims=data.get("victims") or [],
        entities=_extract_entities(data),
        references=context.get("_ref_ids") or [],
        external_sources=context.get("_external_sources") or [],
        status=ReportStatus.DEGRADED if output.degraded else ReportStatus.PUBLISHED,
        model=output.model,
        prompt_version=version,
        tokens=(output.prompt_tokens or 0) + (output.completion_tokens or 0),
        latency_ms=output.latency_ms,
        published_at=now_utc(),
    )
    session.add(report)
    await session.flush()
    return report


def _extract_entities(data: dict) -> list[dict]:
    entities: list[dict] = []
    for key, direction in (("beneficiaries", "positive"), ("victims", "negative")):
        for item in data.get(key) or []:
            if isinstance(item, dict):
                entities.append(
                    {
                        "code": item.get("code"),
                        "name": item.get("name"),
                        "type": item.get("type", "sector"),
                        "direction": direction,
                        "reason": item.get("reason", ""),
                    }
                )
    return entities
