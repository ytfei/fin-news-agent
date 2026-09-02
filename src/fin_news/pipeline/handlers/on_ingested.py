"""news.ingested：把一批资讯交给评分 Agent（flash 模型批量打分）。

重试语义（重要）：
* 模型漏评 → 小批量补打 2 轮；仍缺失才标记 SCORE_FAILED，事件放回队列重试
  （前 RELEASE_GRACE 次不计重试次数，避免把"正常但被漏掉"的资讯刷进死信）
* 资讯已被其它批次评过分 → 补齐下游 news.scored 事件后直接确认，不重复评分
"""
from __future__ import annotations

import time
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.scoring_agent import ScoringAgent
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import EventStatus, EventType, NewsStatus
from fin_news.core.logging import elapsed_ms, get_logger
from fin_news.events.bus import EventBus
from fin_news.models.event import IngestEvent
from fin_news.models.news import NewsItem

logger = get_logger("pipeline.on_ingested")

# 漏评补打：轮数与单轮条数
RESCUE_ROUNDS = 2
# 漏评放回队列的宽限次数，超过后才计为失败（走退避 / 死信）
RELEASE_GRACE = 2


async def handle(
    session: AsyncSession,
    events: list[IngestEvent],
    bus: EventBus,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    news_ids = [e.aggregate_id for e in events]
    started = time.perf_counter()
    logger.info("评分批次开始", events=len(events), news_ids=news_ids[:20])

    if not settings.has_llm_credentials():
        # 未配置模型凭据：放回队列，不消耗重试次数，等配置后自动继续
        logger.warning("未配置模型 API Key，跳过评分（资讯保持 NEW）", count=len(news_ids))
        for event in events:
            await bus.release(event)
        return

    rows = await session.execute(select(NewsItem).where(NewsItem.id.in_(news_ids)))
    item_by_id = {i.id: i for i in rows.scalars().all()}

    pending = [i for i in item_by_id.values() if i.status in (NewsStatus.NEW, NewsStatus.SCORE_FAILED)]
    if not pending:
        # 已被其它批次评过分：确保下游事件存在即可，本批事件直接确认
        published = await _publish_scored(
            session, bus, [i for i in item_by_id.values() if i.score is not None]
        )
        for event in events:
            await bus.ack(event)
        logger.info(
            "评分批次结束（资讯均已评过分，直接确认）",
            events=len(events),
            published=published,
            elapsed_ms=elapsed_ms(started),
        )
        return

    logger.info(
        "评分批次待处理",
        events=len(events),
        pending=len(pending),
        skipped=len(item_by_id) - len(pending),
        news_ids=[i.id for i in pending][:20],
    )
    for item in pending:
        item.status = NewsStatus.SCORING
    await session.flush()

    agent = ScoringAgent(settings)
    try:
        results = await agent.score_items(session, pending)
        scored_ids = set(results.keys())

        # 模型漏评：小批量补打
        missing = [i for i in pending if i.id not in scored_ids]
        for rnd in range(RESCUE_ROUNDS):
            if not missing:
                break
            logger.info("评分漏评，小批量补打", round=rnd + 1, missing=len(missing))
            more = await agent.score_items(session, missing)
            newly = set(more.keys())
            scored_ids |= newly
            missing = [i for i in missing if i.id not in newly]
            if not newly:
                break
    except Exception:  # noqa: BLE001
        logger.exception("批量评分失败", count=len(pending), elapsed_ms=elapsed_ms(started))
        await session.rollback()
        # 回滚后事件仍处于 PROCESSING，由 worker 的 fail 逻辑退避重试
        raise

    missing_ids = {i.id for i in missing}
    for item in missing:
        item.status = NewsStatus.SCORE_FAILED
        item.retry_count = (item.retry_count or 0) + 1
        item.last_error = f"模型漏评（补打 {RESCUE_ROUNDS} 轮仍缺失）"

    published = await _publish_scored(
        session, bus, [i for i in item_by_id.values() if i.score is not None]
    )

    for event in events:
        item = item_by_id.get(event.aggregate_id)
        if item is None or item.score is not None:
            await bus.ack(event)
        elif event.aggregate_id in missing_ids:
            if event.attempts < RELEASE_GRACE:
                await bus.release(event)  # 不消耗重试次数，下轮重新评分
            else:
                await bus.fail(event, "评分漏评（已补打仍缺失）", error_type="ScoringMissed")
        else:
            await bus.ack(event)

    logger.info(
        "评分批次完成",
        total=len(pending),
        scored=len(scored_ids),
        missed=len(missing_ids),
        published=published,
        elapsed_ms=elapsed_ms(started),
    )


async def _publish_scored(
    session: AsyncSession, bus: EventBus, items: Iterable[NewsItem]
) -> int:
    """为已评分的资讯发布 news.scored 事件，返回实际发布条数。

    只对「尚未成功处理过」的资讯发布，避免重复触发向量化与深度分析。
    """
    items = [i for i in items if i.score is not None]
    if not items:
        return 0

    ids = [i.id for i in items]
    done_rows = await session.execute(
        select(IngestEvent.aggregate_id).where(
            IngestEvent.aggregate_id.in_(ids),
            IngestEvent.event_type == EventType.NEWS_SCORED.value,
            IngestEvent.status == EventStatus.DONE,
        )
    )
    already_done = {row[0] for row in done_rows.all()}

    published = 0
    for item in items:
        if item.id in already_done:
            continue
        event_id = await bus.publish(
            EventType.NEWS_SCORED,
            item.id,
            payload={"score": item.score, "band": item.band.value if item.band else None},
            priority=2,
        )
        if event_id is not None:
            published += 1

    logger.info(
        "发布 news.scored 事件",
        requested=len(items),
        published=published,
        skipped_done=len(already_done),
        deduped=len(items) - len(already_done) - published,
    )
    return published
