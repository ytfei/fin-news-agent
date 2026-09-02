"""盘前 / 盘后 Agent：定时生成当日展望与复盘（回答「今天为什么涨跌」）。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.base import AgentOutput, _run_plain_agent
from fin_news.agents.graphs.analysis_graphs import run_analysis
from fin_news.agents.prompts import (
    POST_MARKET_SYSTEM,
    POST_MARKET_USER_TEMPLATE,
    POST_MARKET_VERSION,
    PRE_MARKET_SYSTEM,
    PRE_MARKET_USER_TEMPLATE,
    PRE_MARKET_VERSION,
)
from fin_news.agents.registry import get_agent
from fin_news.agents.tools.market_data import (
    high_score_news,
    is_trading_day,
    latest_trade_date,
    market_snapshot,
    top_list_of_day,
    us_overnight,
)
from fin_news.core.config import Settings, get_settings
from fin_news.core.db import session_scope
from fin_news.core.enums import AgentType, MarketPeriod, ReportStatus
from fin_news.core.logging import get_logger, stage
from fin_news.core.timeutil import now, now_utc
from fin_news.models.analysis import AnalysisReport

logger = get_logger("agents.market")

OVERNIGHT_HOURS = 12

# 上下文预算：盘前盘后改走「ReAct 深度分析」后，prompt 需为多轮工具结果留足空间，
# 故预取只保留 Agent 靠工具难以高效重建的骨架数据，并统一截断。
SNAPSHOT_CHARS = 1500  # 市场快照（指数 / 涨跌家数 / 板块），与 qa_agent 惯例一致
NEWS_INDEX_CHARS = 2000  # 资讯索引：仅标题 + 评分 + id
BRIEF_LIST_CHARS = 800  # 辅助列表（隔夜外盘 / 龙虎榜）


# ----------------------------------------------------------------------
async def run_pre_market(trade_date: date | None = None, settings: Settings | None = None):
    settings = settings or get_settings()
    day = trade_date or now().date()
    async with session_scope() as session:
        if not await is_trading_day(session, day):
            logger.info("非交易日，跳过盘前任务", trade_date=day.isoformat())
            return None
        return await _build_brief(session, day, MarketPeriod.PRE_MARKET, settings)


async def run_post_market(trade_date: date | None = None, settings: Settings | None = None):
    settings = settings or get_settings()
    day = trade_date or now().date()
    async with session_scope() as session:
        if not await is_trading_day(session, day):
            logger.info("非交易日，跳过盘后任务", trade_date=day.isoformat())
            return None
        return await _build_brief(session, day, MarketPeriod.POST_MARKET, settings)


# ----------------------------------------------------------------------
async def _build_brief(
    session: AsyncSession,
    trade_date: date,
    period: MarketPeriod,
    settings: Settings,
) -> AnalysisReport | None:
    if not settings.has_llm_credentials():
        logger.warning("未配置模型 API Key，跳过简报生成", period=period.value)
        return None

    agent_type = AgentType.PRE_MARKET if period == MarketPeriod.PRE_MARKET else AgentType.POST_MARKET
    system_prompt, user_template, version = (
        (PRE_MARKET_SYSTEM, PRE_MARKET_USER_TEMPLATE, PRE_MARKET_VERSION)
        if period == MarketPeriod.PRE_MARKET
        else (POST_MARKET_SYSTEM, POST_MARKET_USER_TEMPLATE, POST_MARKET_VERSION)
    )

    # 分阶段计时：简报链路没有事件化重试，出问题时只能靠日志定位慢在哪一段
    async with stage(logger, "预取上下文", period=period.value) as out:
        context = await _build_context(session, trade_date, period, settings)
        out["news"] = len(context.get("_news_ids") or [])

    # 内部字段不进模板
    template_ctx = {k: v for k, v in context.items() if not k.startswith("_")}

    async with stage(logger, "执行简报 Agent", period=period.value, agent=agent_type.value):
        output: AgentOutput = await _run_brief_agent(
            agent_type, system_prompt, user_template.format(**template_ctx), settings
        )

    report = await _persist_brief(session, trade_date, period, agent_type, version, output, context)
    logger.info(
        "简报生成完成",
        period=period.value,
        trade_date=trade_date.isoformat(),
        report_id=report.id,
        degraded=output.degraded,
        latency_ms=output.latency_ms,
    )
    return report


async def _run_brief_agent(
    agent_type: AgentType,
    system_prompt: str,
    user_prompt: str,
    settings: Settings,
) -> AgentOutput:
    """执行简报 Agent：优先 DeepAgents 图（缓存 + 结构化输出），失败降级单次调用。

    与 analysis_agents._run_analysis 同构。差异：简报的上下文（行情 / 资讯 / 历史 /
    龙虎榜）已由 _build_context 预取并内联进 user_prompt，Agent 侧工具仅用于按需
    补充检索，不再重复预取（避免 token 翻倍）。
    """
    if settings.agent_framework == "langgraph" and settings.use_deep_agents:
        try:
            # 经 registry 拿缓存图：统一入口（deepagents 分支委托 get_analysis_graph）
            graph = get_agent(agent_type, settings)
            # 简报走深度多轮 ReAct（子 agent 并行 + 外部检索），用更宽松的耗时预算
            run = await run_analysis(
                agent_type,
                user_prompt,
                settings,
                graph=graph,
                timeout_seconds=settings.brief_timeout_seconds,
            )
            if run.payload is not None:
                logger.info(
                    "简报 Agent 走 DeepAgents 路径",
                    agent=agent_type.value,
                    latency_ms=run.latency_ms,
                    tokens=(run.prompt_tokens or 0) + (run.completion_tokens or 0),
                )
                return AgentOutput(
                    data=run.payload.model_dump(),
                    model=run.model,
                    prompt_tokens=run.prompt_tokens,
                    completion_tokens=run.completion_tokens,
                    latency_ms=run.latency_ms,
                )
            logger.warning(
                "DeepAgents 未产出结构化结果，降级单次调用",
                agent=agent_type.value,
                error=run.error,
            )
        except Exception as exc:  # noqa: BLE001 - 图构建/执行失败均降级
            logger.warning(
                "DeepAgents 执行失败，降级单次调用",
                agent=agent_type.value,
                error=str(exc)[:300],
            )
    return await _run_plain_agent(system_prompt, user_prompt, settings)


async def _build_context(
    session: AsyncSession, trade_date: date, period: MarketPeriod, settings: Settings
) -> dict[str, Any]:
    now_dt = now()

    if period == MarketPeriod.PRE_MARKET:
        start = now_dt - timedelta(hours=OVERNIGHT_HOURS)
        end = now_dt
    else:
        start = datetime.combine(trade_date, datetime.min.time()).astimezone(now_dt.tzinfo)
        end = now_dt

    news_items = await high_score_news(session, start, end, min_score=5, limit=20)
    market = await market_snapshot(session, trade_date)
    us = await us_overnight(session, trade_date)
    prev_date = await latest_trade_date(session, before=trade_date - timedelta(days=1))
    prev_market = await market_snapshot(session, prev_date) if prev_date else {}
    top_list = await top_list_of_day(session, trade_date, limit=15) if period == MarketPeriod.POST_MARKET else []

    # 历史情境不再预取：改为交给子 agent（newsflow-analyst / attribution-analyst）
    # 按需调用 history_search 深挖。预取一份再让 Agent 查一遍会造成 token 翻倍，
    # 且会架空 ReAct 的检索动机（Agent 无需检索即可作答）。

    # 资讯索引：只给「标题 + 评分 + id」骨架，细节由 Agent 决定深挖哪些条，
    # id 同时用于归因挂 news_id 与填 references。
    news_text = (
        "\n".join(f"- [id:{n['id']}]（评分{n['score']}）{n['title']}" for n in news_items)[
            :NEWS_INDEX_CHARS
        ]
        or "（今日暂无高评分资讯）"
    )

    return {
        "trade_date": trade_date.isoformat(),
        # us_daily 无权限时这里会是空列表，隔夜外盘由 overnight-analyst 用 web_search 补齐
        "us_market": json.dumps(us, ensure_ascii=False)[:BRIEF_LIST_CHARS],
        "prev_market": json.dumps(prev_market, ensure_ascii=False)[:SNAPSHOT_CHARS],
        "market": json.dumps(market, ensure_ascii=False)[:SNAPSHOT_CHARS],
        "news": news_text,
        "top_list": json.dumps(top_list, ensure_ascii=False)[:BRIEF_LIST_CHARS],
        "_news_ids": [n["id"] for n in news_items],
    }


async def _persist_brief(
    session: AsyncSession,
    trade_date: date,
    period: MarketPeriod,
    agent_type: AgentType,
    version: str,
    output: AgentOutput,
    context: dict[str, Any],
) -> AnalysisReport:
    await session.execute(
        update(AnalysisReport)
        .where(
            AnalysisReport.trade_date == trade_date,
            AnalysisReport.period == period,
            AnalysisReport.prompt_version == version,
            AnalysisReport.status.in_(
                [ReportStatus.DRAFT, ReportStatus.PUBLISHED, ReportStatus.DEGRADED]
            ),
        )
        .values(status=ReportStatus.SUPERSEDED)
    )

    data = output.data or {}
    extras = data.get("extras") or {}
    verdict = extras.get("verdict") or {}
    one_liner = str(verdict.get("one_liner") or "").strip()

    # 标题 / 摘要兜底：模型未输出 headline/summary 时，降级用 verdict.one_liner，
    # 再不行才用「日期 + 场次」占位，避免列表页出现空摘要
    headline = str(data.get("headline") or "").strip()
    title = headline or one_liner or f"{trade_date.isoformat()} {period.value}"
    summary = str(data.get("summary") or "").strip() or one_liner

    report = AnalysisReport(
        agent_type=agent_type,
        trade_date=trade_date,
        period=period,
        title=title[:255],
        summary=summary[:2000],
        content=data,
        sentiment=data.get("sentiment") or "neutral",
        impact_level=data.get("impact_level") or "medium",
        horizon=data.get("horizon") or "intraday",
        confidence=float(data.get("confidence", 0.6) or 0.6),
        beneficiaries=data.get("beneficiaries") or [],
        victims=data.get("victims") or [],
        entities=[],
        references=context.get("_news_ids") or [],
        external_sources=[],
        status=ReportStatus.DEGRADED if output.degraded else ReportStatus.PUBLISHED,
        model=output.model,
        prompt_version=version,
        tokens=(output.prompt_tokens or 0) + (output.completion_tokens or 0),
        latency_ms=output.latency_ms,
        published_at=now_utc(),
    )
    # 盘前/盘后的关键结构化字段提升到顶层，便于 API 直接返回
    if isinstance(extras, dict) and extras:
        report.content["extras"] = extras
    session.add(report)
    await session.flush()
    return report
