"""微信公众号文章 Agent：每日筛选高评分资讯，写一篇有「活人感」的公众号文章并向量化入库。

流程：
1. 加载 skills（提示词型注入 system prompt，工具型挂入 Agent 工具）
2. 筛选当日高评分资讯 + 市场快照，构建上下文
3. 跑 DeepAgents（带记忆：article_search 回顾历史已发布文章）
4. 落库 WechatArticle（status=NEW），文章 chunk + 向量化写入 WechatArticleChunk
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from datetime import time as dt_time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.base import _run_plain_agent
from fin_news.agents.graphs.analysis_graphs import build_analysis_graph, run_analysis
from fin_news.agents.prompts import (
    ARTICLE_SCHEMA,
    WECHAT_SYSTEM,
    WECHAT_USER_TEMPLATE,
    WECHAT_VERSION,
)
from fin_news.agents.skills import (
    build_skills_setup,
    enabled_skill_names,
    load_tool_skills,
)
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import AgentType, ArticleStatus
from fin_news.core.logging import get_logger
from fin_news.core.timeutil import MARKET_TZ
from fin_news.models.news import NewsItem
from fin_news.models.wechat import WechatArticle, WechatArticleChunk

logger = get_logger("agents.wechat")

DEFAULT_NEWS_LIMIT = 30


async def write_article(
    session: AsyncSession,
    publish_date: date,
    *,
    skills_dir: str | None = None,
    settings: Settings | None = None,
) -> WechatArticle | None:
    """为指定交易日写一篇公众号文章。无可用资讯时返回 None。"""
    settings = settings or get_settings()

    # 1) 技能装配：提示词型走 DeepAgents 原生（渐进式披露，正文由 Agent 按需
    #    read_file），工具型（tool.py）原生不支持，继续走 extra_tools。
    #    CLI 的 --skills-dir 优先于配置里的搜索路径。
    search_paths = [skills_dir] if skills_dir else settings.skills_search_paths
    skills = build_skills_setup(
        enabled_skill_names(AgentType.WECHAT_ARTICLE, settings),
        search_paths,
        agent=AgentType.WECHAT_ARTICLE.value,
    )
    tool_skills = load_tool_skills([r.skill_dir for r in (skills.refs if skills else ())])

    # 2) 筛选当日高评分资讯 + 市场快照
    news_items = await _select_daily_news(session, publish_date, settings)
    if not news_items:
        logger.warning("当日无可用资讯，跳过写文章", publish_date=str(publish_date))
        return None

    # 提示词型技能不再拼进 system prompt：原生 SkillsMiddleware 只注入
    # name+description+path，正文由 Agent 判定相关后自行 read_file（省 token）
    system_prompt = WECHAT_SYSTEM
    user_prompt = WECHAT_USER_TEMPLATE.format(
        trade_date=publish_date.isoformat(),
        market=await _market_context(session, publish_date),
        news=_format_news_index(news_items),
    )

    logger.info(
        "公众号文章 Agent 开始",
        publish_date=str(publish_date),
        news_count=len(news_items),
        skills=list(skills.names) if skills else [],
        tool_skills=len(tool_skills),
        model=settings.model_for(settings.llm_default_provider, "analysis"),
        prompt_version=WECHAT_VERSION,
    )

    # 3) 构图 + 执行
    graph = build_analysis_graph(
        AgentType.WECHAT_ARTICLE,
        settings,
        system_prompt=system_prompt,
        extra_tools=tool_skills,
        skills=skills,
    )
    run = await run_analysis(
        AgentType.WECHAT_ARTICLE,
        user_prompt,
        settings,
        graph=graph,
        timeout_seconds=settings.brief_timeout_seconds,
        recursion_limit=100,
    )

    # 4) 解析结果（DeepAgents 结构化失败时降级单次调用）
    model = run.model
    if run.payload is not None:
        data = run.payload.model_dump()
    else:
        logger.warning("DeepAgents 未产出结构化结果，降级单次调用", error=run.error)
        fallback = await _run_plain_agent(
            system_prompt, user_prompt, settings, response_schema=ARTICLE_SCHEMA
        )
        data = fallback.data
        model = model or fallback.model

    # 5) 落库
    article = WechatArticle(
        title=str(data.get("title") or "").strip()[:255] or f"{publish_date} 市场综述",
        summary=str(data.get("summary") or "").strip()[:500] or None,
        content=str(data.get("content") or "").strip(),
        status=ArticleStatus.NEW,
        publish_date=publish_date,
        cover_hint=(str(data.get("cover_hint") or "").strip()[:500] or None),
        source_news_ids=[n.id for n in news_items],
        referenced_article_ids=[str(x) for x in (data.get("referenced_article_ids") or [])],
        prompt_version=WECHAT_VERSION,
        model=model,
        tokens=(run.prompt_tokens or 0) + (run.completion_tokens or 0),
        latency_ms=run.latency_ms,
    )
    session.add(article)
    await session.flush()

    # 6) 文章 chunk + 向量化
    chunks = await vectorize_article(session, article, settings)

    logger.info(
        "公众号文章已写入",
        article_id=article.id,
        title=article.title,
        chunks=chunks,
        degraded=run.payload is None,
    )
    return article


async def vectorize_article(session: AsyncSession, article: WechatArticle, settings: Settings | None = None) -> int:
    """把文章正文分块 + 向量化，写入 wechat_article_chunk。返回分块数。"""
    from fin_news.agents.embeddings import get_embedder
    from fin_news.domain.chunking import chunk_text

    settings = settings or get_settings()
    chunks = chunk_text(article.content or "", prefix=f"《{article.title}》\n")
    if not chunks:
        return 0

    embedder = get_embedder(settings)
    vectors = await embedder.embed(chunks)
    model = settings.model_for(settings.embedding_provider, "embedding")
    for idx, (text, vec) in enumerate(zip(chunks, vectors, strict=True)):
        session.add(
            WechatArticleChunk(
                article_id=article.id,
                chunk_index=idx,
                content=text,
                embedding=vec,
                model=model,
            )
        )
    await session.flush()
    return len(chunks)


# ----------------------------------------------------------------------
async def _select_daily_news(
    session: AsyncSession, publish_date: date, settings: Settings, limit: int = DEFAULT_NEWS_LIMIT
) -> list[NewsItem]:
    """筛选当日（Asia/Shanghai 口径）评分高于阈值（非噪声）的资讯，按评分降序。"""
    threshold = settings.score_threshold_vectorize
    day_start = datetime.combine(publish_date, dt_time.min).replace(tzinfo=MARKET_TZ)
    day_end = day_start + timedelta(days=1)
    rows = await session.execute(
        select(NewsItem)
        .where(
            NewsItem.publish_time >= day_start,
            NewsItem.publish_time < day_end,
            NewsItem.score.is_not(None),
            NewsItem.score > threshold,
        )
        .order_by(NewsItem.score.desc(), NewsItem.publish_time.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def _market_context(session: AsyncSession, publish_date: date) -> str:
    from fin_news.agents.tools.market_data import market_snapshot

    try:
        data = await market_snapshot(session, publish_date)
    except Exception as exc:  # noqa: BLE001 - 快照失败不阻断写文章
        logger.warning("市场快照获取失败", error=str(exc)[:200])
        data = {}
    return json.dumps(data, ensure_ascii=False)[:2000]


def _format_news_index(items: list[NewsItem]) -> str:
    lines = [
        f"- [{n.id}] ({n.score}分，{n.band.value if n.band else '未分档'}) {n.title}"
        for n in items
    ]
    return "\n".join(lines)
