"""news.embedded：按评分路由到宏观政策 / 行业 / 个股 Agent 做深度分析。"""
from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.analysis_agents import analyze_news
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import EventType, NewsStatus
from fin_news.core.logging import bind_context, elapsed_ms, get_logger, unbind_context
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
    logger.info("深度分析批次开始", events=len(events), news_ids=news_ids[:20])

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
            logger.debug("资讯不存在或状态已变更，直接确认", news_id=event.aggregate_id)
            await bus.ack(event)
            await session.commit()
            continue

        agent_type = agent_for_score(news.score)
        if agent_type is None:
            logger.info("评分未达分析阈值，跳过深度分析", news_id=news.id, score=news.score)
            await bus.ack(event)
            await session.commit()
            continue

        # 绑定到上下文：分析 Agent 内部（含图执行、降级）的日志自动带 news_id / agent
        bind_context(news_id=news.id, agent=agent_type.value)
        started = time.perf_counter()
        try:
            report = await analyze_news(session, news, settings)
            if report is None:
                logger.info("深度分析未产出报告", news_id=news.id, agent=agent_type.value)
                await bus.ack(event)
                await session.commit()
                continue
            await bus.publish(
                EventType.ANALYSIS_PUBLISHED,
                report.id,
                payload={"agent_type": agent_type.value, "news_id": news.id},
                priority=1,
            )
            await bus.ack(event)
            logger.info(
                "深度分析事件已确认（报告已发布）",
                news_id=news.id,
                agent=agent_type.value,
                report_id=report.id,
                status=getattr(report.status, "value", report.status),
                elapsed_ms=elapsed_ms(started),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "深度分析失败",
                news_id=news.id,
                agent=agent_type.value,
                elapsed_ms=elapsed_ms(started),
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
            )
            news.status = NewsStatus.ANALYSIS_FAILED
            news.analysis_status = "FAILED"
            news.retry_count = (news.retry_count or 0) + 1
            news.last_error = str(exc)[:500]
            await bus.fail(event, str(exc)[:300], error_type="AnalysisFailed")
        finally:
            unbind_context("news_id", "agent")
        # 单条分析耗时长，逐条提交，保证已完成的结果立即可见且不因异常整体回滚
        await session.commit()
