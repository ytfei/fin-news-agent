"""news.embedded：按评分路由到宏观政策 / 行业 / 个股 Agent 做深度分析。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.analysis_agents import analyze_news
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import EventType, NewsStatus
from fin_news.core.logging import get_logger
from fin_news.domain.scoring import agent_for_score
from fin_news.events.bus import EventBus
from fin_news.models.event import IngestEvent
from fin_news.models.news import NewsItem

logger = get_logger("pipeline.on_embedded")


async def handle(
    session: AsyncSession,
    events: list[IngestEvent],
    bus: EventBus,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    news_ids = [e.aggregate_id for e in events]

    rows = await session.execute(
        select(NewsItem).where(
            NewsItem.id.in_(news_ids),
            NewsItem.status.in_([NewsStatus.EMBEDDED, NewsStatus.ANALYSIS_FAILED]),
        )
    )
    items = {n.id: n for n in rows.scalars().all()}

    if not settings.has_llm_credentials():
        logger.warning("未配置模型 API Key，跳过深度分析", count=len(news_ids))
        for event in events:
            await bus.release(event)
        return

    for event in events:
        news = items.get(event.aggregate_id)
        if news is None:
            await bus.ack(event)
            continue

        agent_type = agent_for_score(news.score)
        if agent_type is None:
            await bus.ack(event)
            continue

        try:
            report = await analyze_news(session, news, settings)
            if report is None:
                await bus.ack(event)
                continue
            await bus.publish(
                EventType.ANALYSIS_PUBLISHED,
                report.id,
                payload={"agent_type": agent_type.value, "news_id": news.id},
                priority=1,
            )
            await bus.ack(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("深度分析失败", news_id=news.id, agent=agent_type.value, error=str(exc))
            news.status = NewsStatus.ANALYSIS_FAILED
            news.analysis_status = "FAILED"
            news.retry_count = (news.retry_count or 0) + 1
            news.last_error = str(exc)[:500]
            await bus.fail(event, str(exc)[:300], error_type="AnalysisFailed")
