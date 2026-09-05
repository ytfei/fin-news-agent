"""news.embedded：按评分路由到宏观政策 / 行业 / 个股 Agent 做深度分析。

并发模型
--------
深度分析是整条链路最慢的环节（实测单条平均 70 秒、P95 361 秒）。原先**串行**
处理一批事件（`worker_batch_limit` 默认 50）需要近一小时，是事件积压的主要来源。
这里改为**受控并发**：

* 并发上限由 `analysis_concurrency` 控制。注意该配置此前对分析链路**从未生效**
  ——它只在已无调用点的 `agents/base.py:run_agent` 里被引用过，本次才真正接上。
* 每个并发任务持有**独立 session + 独立事务**：`AsyncSession` 不是并发安全的，
  多个协程不能共用同一个；独立事务还保证单条失败只回滚自己，不污染同批其他资讯。
* 事件确认（ack / fail）在各任务自己的事务内完成，让「分析 → 报告落库 → 事件
  状态」三者原子一致。

另两项等价优化（不改变任何分析结果）：
* 市场快照每批只查一次共享给全部资讯（同一交易日内结果完全相同）
* 已有当前版本有效报告的资讯直接跳过，避免重复烧钱
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.analysis_agents import AGENT_CONFIG, analyze_news_by_id
from fin_news.agents.llm import get_semaphore
from fin_news.agents.tools.market_data import latest_trade_date, market_snapshot
from fin_news.core.config import Settings, get_settings
from fin_news.core.db import session_scope
from fin_news.core.enums import EventType, NewsStatus, ReportStatus
from fin_news.core.logging import bind_context, elapsed_ms, get_logger, unbind_context
from fin_news.domain.scoring import agent_for_score
from fin_news.events.bus import EventBus
from fin_news.models.analysis import AnalysisReport
from fin_news.models.event import IngestEvent
from fin_news.models.news import NewsItem

logger = get_logger("pipeline.on_embedded")

# 视为「已有有效报告」的状态 —— 与库上 uq_report_news_agent 唯一索引的谓词
# 保持一致，被 SUPERSEDED 的旧报告不算数
_ACTIVE_REPORT_STATUS = (
    ReportStatus.DRAFT,
    ReportStatus.PUBLISHED,
    ReportStatus.DEGRADED,
)

# 单条资讯的分析器：(session, news_id, settings, market_json) -> 报告或 None
Analyzer = Callable[..., Awaitable[AnalysisReport | None]]


async def handle(
    session: AsyncSession,
    events: list[IngestEvent],
    bus: EventBus,
    settings: Settings | None = None,
    *,
    analyzer: Analyzer | None = None,
) -> None:
    """处理一批 news.embedded 事件。

    analyzer 可注入（默认 `analyze_news_by_id`）：测试时换成假实现即可在不碰
    模型与 DB 的前提下验证并发上限、失败隔离、去重跳过等行为。
    """
    settings = settings or get_settings()
    run_analysis = analyzer or analyze_news_by_id
    started = time.perf_counter()
    news_ids = [e.aggregate_id for e in events]
    logger.info("深度分析批次开始", events=len(events), news_ids=news_ids[:20])

    rows = await session.execute(
        select(NewsItem).where(
            NewsItem.id.in_(news_ids),
            NewsItem.status.in_([NewsStatus.EMBEDDED, NewsStatus.ANALYSIS_FAILED]),
        )
    )
    items = {n.id: n for n in rows.scalars().all()}
    # 准备阶段信息已拿全，立即提交释放事务：外层若长期持有事务与连接，
    # 会挤压并发任务的连接池额度（pool_size=10 + max_overflow=20）
    await session.commit()

    if not settings.has_llm_credentials():
        logger.warning("未配置模型 API Key，跳过深度分析", count=len(news_ids))
        for event in events:
            await bus.release(event)
        return

    # ---- 1) 预取市场快照：本批一次，全部资讯共享 ----
    market_json = await _prefetch_market(session)

    # ---- 2) 路由 + 过滤：划分「待分析 / 直接确认 / 已有报告跳过」----
    candidate_ids = [n.id for n in items.values() if n.score is not None]
    done_keys = await _existing_report_keys(session, candidate_ids)

    todo: list[tuple[int, int, str]] = []  # (event_id, news_id, agent)
    acked = 0
    skipped_existing = 0
    skipped_degraded = 0

    for event in events:
        news = items.get(event.aggregate_id)
        if news is None:
            logger.debug("资讯不存在或状态已变更，直接确认", news_id=event.aggregate_id)
            await bus.ack(event)
            acked += 1
            continue

        if news.score is None:
            # 必须拦掉：agent_for_score(None) 会抛 ValueError，炸掉整批
            logger.debug("资讯未评分，直接确认", news_id=news.id)
            await bus.ack(event)
            acked += 1
            continue

        agent_type = agent_for_score(news.score)
        if agent_type is None:
            logger.info("评分未达分析阈值，跳过深度分析", news_id=news.id, score=news.score)
            await bus.ack(event)
            acked += 1
            continue

        _system_prompt, _template, version = AGENT_CONFIG[agent_type]
        existing_status = done_keys.get((news.id, agent_type, version))
        if settings.analysis_skip_existing and existing_status is not None:
            skipped_existing += 1
            if existing_status == ReportStatus.DEGRADED:
                skipped_degraded += 1
            await bus.ack(event)
            acked += 1
            continue

        todo.append((event.id, news.id, agent_type.value))

    await session.commit()  # 统一提交过滤阶段产生的 ack

    if skipped_existing:
        # 降级报告也跳过（避免重复烧钱），但单独计数并告警，不让质量问题被藏起来
        logger.warning(
            "跳过已有报告的资讯",
            skipped=skipped_existing,
            degraded=skipped_degraded,
            hint="如需强制重跑请设 ANALYSIS_SKIP_EXISTING=false",
        )

    if not todo:
        logger.info(
            "深度分析批次结束（无可分析资讯）",
            events=len(events),
            acked=acked,
            elapsed_ms=elapsed_ms(started),
        )
        return

    # ---- 3) 并发执行：信号量控上限，每任务独立 session ----
    semaphore = get_semaphore("analysis", settings)
    worker_id = bus.worker_id
    logger.info(
        "深度分析并发开始",
        pending=len(todo),
        concurrency=settings.analysis_concurrency,
        shared_market=market_json is not None,
    )

    async def _analyze_one(event_id: int, news_id: int, agent: str) -> str:
        async with semaphore:
            # contextvars 按 task 隔离，并发下不会串扰；必须成对 bind/unbind
            bind_context(news_id=news_id, agent=agent)
            try:
                return await _analyze_own_tx(
                    event_id, news_id, agent, market_json, settings, worker_id,
                    run_analysis,
                )
            finally:
                unbind_context("news_id", "agent")

    results = await asyncio.gather(
        *(_analyze_one(eid, nid, ag) for eid, nid, ag in todo),
        return_exceptions=True,
    )

    ok = skipped = failed = 0
    for r in results:
        if isinstance(r, BaseException):
            failed += 1
            logger.error("并发任务异常", error=f"{type(r).__name__}: {str(r)[:200]}")
        elif r == "ok":
            ok += 1
        elif r == "skipped":
            skipped += 1
        else:
            failed += 1

    logger.info(
        "深度分析批次结束",
        events=len(events),
        analyzed=len(todo),
        ok=ok,
        skipped=skipped,
        failed=failed,
        acked=acked,
        skipped_existing=skipped_existing,
        concurrency=settings.analysis_concurrency,
        elapsed_ms=elapsed_ms(started),
    )


async def _prefetch_market(session: AsyncSession) -> str | None:
    """预取本批共享的市场快照（一次查询，全部资讯复用）。

    同一交易日内 `market_snapshot` 对所有资讯返回**完全相同**的结果，逐条查询
    等于把同一条 SQL 执行 N 遍。这里刻意复用 `_build_context` 的序列化方式
    （`json.dumps` + 截断 2000 字符），保证共享与自查两条路径结果逐字节一致。

    返回 None 表示预取失败，交由各条资讯自行兜底查询。
    """
    try:
        trade_date = await latest_trade_date(session) or date.today()
        market = await market_snapshot(session, trade_date)
    except Exception as exc:  # noqa: BLE001 - 预取失败不应阻断，回落为逐条查询
        logger.warning("市场快照预取失败，交由各条资讯自行查询", error=str(exc)[:200])
        return None
    return json.dumps(market, ensure_ascii=False)[:2000]


async def _existing_report_keys(
    session: AsyncSession, news_ids: list[int]
) -> dict[tuple[int, object, str], ReportStatus]:
    """查出这些资讯已有的「有效报告」，键为 (news_id, agent_type, prompt_version)。

    口径与库上 `uq_report_news_agent` 唯一索引的谓词一致：只认 DRAFT /
    PUBLISHED / DEGRADED，被 SUPERSEDED 的旧报告不算数。
    """
    if not news_ids:
        return {}
    rows = await session.execute(
        select(
            AnalysisReport.news_id,
            AnalysisReport.agent_type,
            AnalysisReport.prompt_version,
            AnalysisReport.status,
        ).where(
            AnalysisReport.news_id.in_(news_ids),
            AnalysisReport.status.in_(_ACTIVE_REPORT_STATUS),
        )
    )
    return {(r[0], r[1], r[2]): r[3] for r in rows.all()}


async def _analyze_own_tx(
    event_id: int,
    news_id: int,
    agent: str,
    market_json: str | None,
    settings: Settings,
    worker_id: str,
    analyzer: Analyzer,
) -> str:
    """分析单条资讯：独立 session + 独立事务，返回 'ok' / 'skipped' / 'failed'。

    独立事务是关键：单条失败只回滚自己，同批其他资讯不受影响；事件确认与报告
    落库在同一事务内，不会出现「报告写了但事件没确认」的不一致。
    """
    async with session_scope() as s:
        own_bus = EventBus(s, worker_id)
        try:
            report = await analyzer(s, news_id, settings, market_json=market_json)
        except Exception as exc:  # noqa: BLE001
            # 先回滚分析过程中的部分更改，再只保留失败标记
            await s.rollback()
            logger.exception(
                "深度分析失败",
                news_id=news_id,
                agent=agent,
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
            )
            news = (
                await s.execute(select(NewsItem).where(NewsItem.id == news_id))
            ).scalar_one_or_none()
            if news is not None:
                news.status = NewsStatus.ANALYSIS_FAILED
                news.analysis_status = "FAILED"
                news.retry_count = (news.retry_count or 0) + 1
                news.last_error = str(exc)[:500]
            await own_bus.fail(event_id, str(exc)[:300], error_type="AnalysisFailed")
            return "failed"

        if report is None:
            logger.info("深度分析未产出报告", news_id=news_id, agent=agent)
            await own_bus.ack(event_id)
            return "skipped"

        await own_bus.publish(
            EventType.ANALYSIS_PUBLISHED,
            report.id,
            payload={"agent_type": agent, "news_id": news_id},
            priority=1,
        )
        await own_bus.ack(event_id)
        logger.info(
            "深度分析事件已确认（报告已发布）",
            news_id=news_id,
            agent=agent,
            report_id=report.id,
            status=getattr(report.status, "value", report.status),
        )
        return "ok"
