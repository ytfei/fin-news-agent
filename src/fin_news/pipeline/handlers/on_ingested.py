"""news.ingested：把一批资讯交给评分 Agent（flash 模型批量打分）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.scoring_agent import ScoringAgent
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import EventType, NewsStatus
from fin_news.core.logging import get_logger
from fin_news.events.bus import EventBus
from fin_news.models.event import IngestEvent
from fin_news.models.news import NewsItem

logger = get_logger("pipeline.on_ingested")


async def handle(
    session: AsyncSession,
    events: list[IngestEvent],
    bus: EventBus,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    news_ids = [e.aggregate_id for e in events]

    if not settings.has_llm_credentials():
        # 未配置模型凭据：直接跳过（不重试，避免刷死信），等配置后由运维重跑
        logger.warning("未配置模型 API Key，跳过评分（资讯保持 NEW）", count=len(news_ids))
        for event in events:
            await bus.release(event)
        return

    rows = await session.execute(
        select(NewsItem).where(
            NewsItem.id.in_(news_ids),
            NewsItem.status.in_([NewsStatus.NEW, NewsStatus.SCORE_FAILED]),
        )
    )
    items = list(rows.scalars().all())

    if not items:
        for event in events:
            await bus.ack(event)
        return

    for item in items:
        item.status = NewsStatus.SCORING
    await session.flush()

    scored_ids: set[int] = set()
    try:
        agent = ScoringAgent(settings)
        results = await agent.score_items(session, items)
        scored_ids = set(results.keys())
    except Exception as exc:  # noqa: BLE001
        logger.exception("批量评分失败", count=len(items), error=str(exc))
        await session.rollback()
        # 回滚后事件仍处于 PROCESSING，重新放回队列由 worker 的 fail 逻辑处理
        raise

    for item in items:
        if item.status == NewsStatus.SCORED:
            await bus.publish(
                EventType.NEWS_SCORED,
                item.id,
                payload={"score": item.score, "band": item.band.value if item.band else None},
                priority=2,
            )

    for event in events:
        if event.aggregate_id in scored_ids or event.aggregate_id not in news_ids:
            await bus.ack(event)
        else:
            await bus.fail(event, "评分未覆盖该资讯", error_type="ScoringMissed")

    logger.info("评分批次完成", total=len(items), scored=len(scored_ids))
