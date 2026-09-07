"""时效策略服务：过期事件批量清理 + 手动触发插队。

两类职责围绕同一个主题——「深度分析事件的时效与优先级」：

* ``expire_stale_events``  定时任务调用，批量 ACK 超期的 PENDING news.embedded
  事件，并把对应资讯标记为 ``EXPIRED``。用一条 CTE SQL 一次处理，避免逐条
  ``bus.ack()`` 的 N+1 开销。
* ``request_analysis``     Web / Mobile 手动触发「生成报告」。**必须处理软去重
  分支**：``EventBus.publish`` 对同聚合 + 同事件类型的 PENDING/PROCESSING 会
  静默忽略（``ON CONFLICT DO NOTHING``），积压场景下直接 publish 等于「点了
  没反应」。这里先查已有事件，决定「提升优先级插队」还是「发布新事件」。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import EventStatus, EventType, NewsStatus
from fin_news.core.logging import get_logger
from fin_news.core.timeutil import now
from fin_news.events.bus import EventBus
from fin_news.models.event import IngestEvent
from fin_news.models.news import NewsItem

logger = get_logger("services.expire")


async def expire_stale_events(
    session: AsyncSession,
    *,
    max_age_hours: int,
    limit: int = 2000,
) -> dict[str, int]:
    """批量 ACK「不再自动分析」的 PENDING news.embedded 事件并标记 EXPIRED。

    两类事件：
    1. 低价值个股（STOCK 档，score 4-5）：永不自动分析，改由用户手动触发。
    2. 超时效窗口（publish_time 距今超过 max_age_hours，仅 max_age_hours > 0 时启用）。

    只扫 ``status = PENDING``（不碰 PROCESSING，避免干扰正在执行的分析）；跳过
    手动触发（``payload.manual``）的事件。

    实现要点：先 ACK 事件并 ``RETURNING aggregate_id``，再只用这些「确实被 ACK」
    的资讯 id 去标 EXPIRED——若在两步之间事件恰好被 worker 消费（变 PROCESSING），
    第一步的 ``WHERE status = 'PENDING'`` 会挡住，不会把正在分析的资讯标过期。
    """
    conditions = ["(n.score > 3 AND n.score <= 5)"]  # STOCK 档（低价值个股）永不自动分析
    params: dict = {"limit": limit}
    if max_age_hours > 0:
        conditions.append("(n.publish_time < :cutoff)")
        params["cutoff"] = now() - timedelta(hours=max_age_hours)
    where_clause = " OR ".join(conditions)

    acked_news_ids = (
        await session.execute(
            text(
                f"""
                WITH stale AS (
                    SELECT e.id AS event_id, e.aggregate_id AS news_id
                    FROM ingest_event e
                    JOIN news_item n ON n.id = e.aggregate_id
                    WHERE e.event_type = 'news.embedded'
                      AND e.status = 'PENDING'
                      AND COALESCE(e.payload->>'manual', 'false') <> 'true'
                      AND ({where_clause})
                    ORDER BY e.id
                    LIMIT :limit
                )
                UPDATE ingest_event e
                SET status = 'DONE',
                    processed_at = now(),
                    locked_by = NULL,
                    last_error = NULL
                FROM stale s
                WHERE e.id = s.event_id AND e.status = 'PENDING'
                RETURNING e.aggregate_id
                """
            ),
            params,
        )
    ).scalars().all()

    if not acked_news_ids:
        return {"events_acked": 0, "news_expired": 0}

    events_acked = len(acked_news_ids)
    news_ids = sorted(set(acked_news_ids))

    result = await session.execute(
        update(NewsItem)
        .where(
            NewsItem.id.in_(news_ids),
            NewsItem.status.in_([NewsStatus.EMBEDDED, NewsStatus.ANALYSIS_FAILED]),
        )
        .values(status=NewsStatus.EXPIRED)
    )
    news_expired = result.rowcount or 0

    logger.info(
        "过期事件批量清理",
        events_acked=events_acked,
        news_expired=news_expired,
        window_hours=max_age_hours,
    )
    return {"events_acked": events_acked, "news_expired": news_expired}


async def request_analysis(
    session: AsyncSession,
    news_id: int,
    *,
    force: bool = False,
    settings: Settings | None = None,
) -> dict | None:
    """请求分析某条资讯：已有待处理事件则提升优先级插队，否则发布高优先级事件。

    ``force=True`` 会在事件 payload 里带 ``force`` 标记，让 handler 绕过「已有报告
    跳过」，用于强制重跑。手动触发不受 24 小时时效窗口限制（payload.manual）。

    返回 None 表示资讯不存在（调用方据此抛 404）。
    """
    settings = settings or get_settings()
    news = await session.get(NewsItem, news_id)
    if news is None:
        return None

    # EXPIRED 回填为 EMBEDDED，使其重新满足 handler 的查询条件
    # （status IN (EMBEDDED, ANALYSIS_FAILED)），逻辑复用而非特判。
    if news.status == NewsStatus.EXPIRED:
        news.status = NewsStatus.EMBEDDED

    # 查已有待处理事件（软去重分支的关键：不能直接 publish）
    existing = (
        await session.execute(
            select(IngestEvent.id, IngestEvent.status)
            .where(
                IngestEvent.event_type == EventType.NEWS_EMBEDDED.value,
                IngestEvent.aggregate_id == news_id,
                IngestEvent.status.in_([EventStatus.PENDING, EventStatus.PROCESSING]),
            )
        )
    ).first()

    payload = {"manual": True, "force": force}

    if existing is not None:
        event_id, status = existing
        if status == EventStatus.PROCESSING:
            # 已在处理中，无需重复触发
            return {"news_id": news_id, "queued": False, "in_progress": True}
        # PENDING → 提升优先级插队（幂等 UPDATE，不会灌水）
        await session.execute(
            update(IngestEvent)
            .where(IngestEvent.id == event_id)
            .values(priority=settings.manual_priority, payload=payload)
        )
        return {
            "news_id": news_id,
            "queued": True,
            "in_progress": False,
            "priority": settings.manual_priority,
            "force": force,
            "escalated": True,
        }

    # 无待处理事件 → 发布高优先级事件（DONE 状态不触发软去重，可正常发布）
    bus = EventBus(session)
    await bus.publish(
        EventType.NEWS_EMBEDDED,
        news_id,
        payload=payload,
        priority=settings.manual_priority,
    )
    return {
        "news_id": news_id,
        "queued": True,
        "in_progress": False,
        "priority": settings.manual_priority,
        "force": force,
        "escalated": False,
    }
