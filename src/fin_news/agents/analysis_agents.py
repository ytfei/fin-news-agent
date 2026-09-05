"""深度分析 Agent：按评分路由到宏观政策 / 行业 / 个股 Agent，产出结构化报告。"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.base import AgentOutput, _run_plain_agent
from fin_news.agents.graphs.analysis_graphs import run_analysis
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
from fin_news.agents.registry import get_agent
from fin_news.agents.tools.market_data import latest_trade_date, market_snapshot
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import AgentType, NewsStatus, ReportStatus
from fin_news.core.logging import get_logger, stage
from fin_news.core.timeutil import now_utc
from fin_news.domain.scoring import agent_for_score
from fin_news.domain.textutil import truncate
from fin_news.models.analysis import AnalysisReport
from fin_news.models.news import NewsItem
from fin_news.observability import AgentRunTracker, digest_of

logger = get_logger("agents.analysis")

AGENT_CONFIG: dict[AgentType, tuple[str, str, str]] = {
    # agent_type -> (system_prompt, user_template, version)
    AgentType.MACRO_POLICY: (MACRO_SYSTEM, MACRO_USER_TEMPLATE, MACRO_VERSION),
    AgentType.INDUSTRY: (INDUSTRY_SYSTEM, INDUSTRY_USER_TEMPLATE, INDUSTRY_VERSION),
    AgentType.STOCK: (STOCK_SYSTEM, STOCK_USER_TEMPLATE, STOCK_VERSION),
}

MAX_CONTENT_CHARS = 3000


async def analyze_news(
    session: AsyncSession,
    news: NewsItem,
    settings: Settings | None = None,
    *,
    market_json: str | None = None,
) -> AnalysisReport | None:
    """对单条资讯执行深度分析，并落库。

    market_json：本批共享的市场快照 JSON 字符串。同一交易日内所有资讯的市场
    快照完全相同，批量并发分析时由调用方预取一次传入，可省掉 N-1 次重复查询。
    不传时内部自行查询（行为与原先完全一致）。
    """
    settings = settings or get_settings()
    agent_type = agent_for_score(news.score)
    if agent_type is None:
        return None

    system_prompt, user_template, version = AGENT_CONFIG[agent_type]
    news.status = NewsStatus.ANALYZING
    news.analysis_status = "PENDING"
    await session.flush()

    async with stage(
        logger,
        "深度分析 Agent",
        news_id=news.id,
        agent=agent_type.value,
        score=news.score,
        framework=(
            "deepagents"
            if settings.agent_framework == "langgraph" and settings.use_deep_agents
            else "legacy"
        ),
        model=settings.model_for(settings.llm_default_provider, "analysis"),
        prompt_version=version,
        timeout_seconds=settings.analysis_timeout_seconds,
    ) as out:
        context = await _build_context(session, news, agent_type, settings, market_json=market_json)
        user_prompt = user_template.format(**context)
        logger.debug(
            "分析上下文已构建",
            news_id=news.id,
            agent=agent_type.value,
            prompt_chars=len(user_prompt),
        )

        # 运行埋点：包住「真正的 LLM 执行」，因此延迟含降级重试的完整耗时，
        # 与用户感知一致。埋点自身异常不会冒泡（见 tracker 实现）。
        async with AgentRunTracker(
            agent_type,
            subject_type="news",
            subject_id=str(news.id),
            prompt_version=version,
            input_digest=digest_of(user_prompt),
        ) as run:
            output: AgentOutput = await _run_analysis(agent_type, system_prompt, user_prompt, settings)
            run.finish(output)
        out.update(
            path="deepagents" if not output.degraded else "legacy(降级)",
            model=output.model,
            latency_ms=output.latency_ms,
            prompt_tokens=output.prompt_tokens,
            completion_tokens=output.completion_tokens,
            degraded=output.degraded,
        )

        report = await _persist(session, news, agent_type, version, output)
        news.status = NewsStatus.ANALYZED
        news.analysis_status = "DONE"
        news.retry_count = 0
        news.last_error = None
        await session.flush()

        out["report_id"] = report.id
        logger.info(
            "分析报告已落库",
            agent=agent_type.value,
            news_id=news.id,
            report_id=report.id,
            degraded=output.degraded,
        )
        return report


async def analyze_news_by_id(
    session: AsyncSession,
    news_id: int,
    settings: Settings | None = None,
    *,
    market_json: str | None = None,
) -> AnalysisReport | None:
    """按 id 查出资讯再分析。

    并发安全：内部自己查 NewsItem，调用方只要给每个并发任务一个**独立 session**
    即可（AsyncSession 不是并发安全的，不能多个协程共用同一个）。
    """
    news = (
        await session.execute(select(NewsItem).where(NewsItem.id == news_id))
    ).scalar_one_or_none()
    if news is None:
        return None
    return await analyze_news(session, news, settings, market_json=market_json)


# ----------------------------------------------------------------------
async def _run_analysis(
    agent_type: AgentType,
    system_prompt: str,
    user_prompt: str,
    settings: Settings,
) -> AgentOutput:
    """执行分析：优先 DeepAgents 图（缓存 + 结构化输出），失败降级单次调用。"""
    if settings.agent_framework == "langgraph" and settings.use_deep_agents:
        try:
            logger.info(
                "DeepAgents 图执行开始",
                agent=agent_type.value,
                timeout_seconds=settings.analysis_timeout_seconds,
            )
            # 经 registry 拿缓存图：统一入口（deepagents 分支内部委托 get_analysis_graph）
            graph = get_agent(agent_type, settings)
            run = await run_analysis(agent_type, user_prompt, settings, graph=graph)
            logger.info(
                "DeepAgents 图执行结束",
                agent=agent_type.value,
                structured=run.payload is not None,
                model=run.model,
                latency_ms=run.latency_ms,
                prompt_tokens=run.prompt_tokens,
                completion_tokens=run.completion_tokens,
                error=run.error,
            )
            if run.payload is not None:
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
    logger.info("单次结构化调用开始", agent=agent_type.value)
    return await _run_plain_agent(system_prompt, user_prompt, settings)


# ----------------------------------------------------------------------
async def _build_context(
    session: AsyncSession,
    news: NewsItem,
    agent_type: AgentType,
    settings: Settings,
    *,
    market_json: str | None = None,
) -> dict:
    """构建分析上下文。

    market_json 由批量调用方预取一次后共享：同一交易日内所有资讯的市场快照
    完全相同，逐条查询等于把同一条 SQL 执行 N 遍。传入时跳过查询（等价优化）。
    """
    title = news.title or ""
    content, _ = truncate(news.content or title, MAX_CONTENT_CHARS)

    # 市场快照：廉价（纯 DB 查询）且确定需要，保留预取。
    # 历史检索 / 外部检索已交给 Agent 的工具按需调用（不再预取塞进 prompt，
    # 避免「预取一份 + Agent 再查一遍」的 token 翻倍——见 05-agent-refactor-design.md 2.7）。
    if market_json is None:
        try:
            trade_date = await latest_trade_date(session) or date.today()
            market = await market_snapshot(session, trade_date)
        except Exception as exc:  # noqa: BLE001
            logger.warning("市场快照获取失败", error=str(exc)[:200])
            market = {}
        market_json = json.dumps(market, ensure_ascii=False)[:2000]

    return {
        "title": title,
        "src_name": news.src_name or news.src or "未知来源",
        "publish_time": news.publish_time.strftime("%Y-%m-%d %H:%M") if news.publish_time else "未知",
        "score": news.score,
        "band": news.band.value if news.band else "",
        "content": content,
        "market": market_json,
    }


async def _persist(
    session: AsyncSession,
    news: NewsItem,
    agent_type: AgentType,
    version: str,
    output: AgentOutput,
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
        # 历史检索 / 外部检索已交给 Agent 工具按需调用，引用信息暂不在分析报告中回填；
        # 待工具层改造（session 注入 + 结果缓存）后再从工具调用结果回补 references。
        references=[],
        external_sources=[],
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
