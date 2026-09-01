"""命令行工具（运维与本地调试）。

用法：
    uv run python -m fin_news.cli ingest           # 手动跑一次增量接入
    uv run python -m fin_news.cli pipeline         # 消费事件（跑一轮）
    uv run python -m fin_news.cli worker           # 常驻 worker
    uv run python -m fin_news.cli score            # 给待评分资讯打分
    uv run python -m fin_news.cli premarket        # 生成盘前简报
    uv run python -m fin_news.cli postmarket       # 生成盘后简报
    uv run python -m fin_news.cli status           # 查看积压与统计
    uv run python -m fin_news.cli selftest         # 数据源连通性自检
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from fin_news.core.config import get_settings
from fin_news.core.logging import configure_logging, get_logger

logger = get_logger("cli")


async def _cmd_ingest() -> int:
    from fin_news.core.db import init_db
    from fin_news.ingestion.service import IngestionService

    await init_db()
    results = await IngestionService().run_all()
    for r in results:
        print(
            f"[{r.status:8}] {r.source_key}: fetched={r.fetched} "
            f"inserted={r.inserted} duplicates={r.duplicates} filtered={r.filtered}"
        )
    return 0


async def _cmd_pipeline(once: bool = True) -> int:
    from fin_news.core.db import init_db
    from fin_news.pipeline.worker import PipelineWorker

    await init_db()
    worker = PipelineWorker()
    if once:
        await worker.reclaim()
        n = await worker.tick()
        print(f"本轮处理事件：{n}")
        return 0
    await worker.run_forever()
    return 0


async def _cmd_score() -> int:
    from fin_news.agents.scoring_agent import ScoringAgent
    from fin_news.core.db import init_db, session_scope

    await init_db()
    async with session_scope() as session:
        n = await ScoringAgent().score_pending(session)
        print(f"已评分：{n}")
    return 0


async def _cmd_market(period: str) -> int:
    from fin_news.agents.market_agents import run_post_market, run_pre_market
    from fin_news.core.db import init_db

    await init_db()
    report = await run_pre_market() if period == "pre" else await run_post_market()
    if report is None:
        print("未生成简报（非交易日或未配置模型 Key）")
        return 1
    print(f"简报已生成：{report.id} / {report.title}")
    return 0


async def _cmd_status() -> int:
    from sqlalchemy import func, select

    from fin_news.core.db import init_db, session_scope
    from fin_news.events.bus import EventBus
    from fin_news.models.analysis import IngestCursor
    from fin_news.models.news import NewsItem

    await init_db()
    async with session_scope() as session:
        backlog = await EventBus(session).backlog()
        total = await session.scalar(select(func.count()).select_from(NewsItem))
        scored = await session.scalar(
            select(func.count()).select_from(NewsItem).where(NewsItem.score.is_not(None))
        )
        print(f"积压：{backlog}")
        print(f"资讯总数：{total}，已评分：{scored}")
        rows = (await session.execute(select(IngestCursor))).scalars().all()
        for c in rows:
            print(
                f"  位点 {c.source_key}: {c.cursor_time} 状态={c.last_status} "
                f"上次条数={c.last_count} enabled={c.enabled}"
            )
    return 0


async def _cmd_selftest() -> int:
    """数据源与模型连通性自检。"""
    from fin_news.ingestion.tushare_client import TusharePermissionError, get_tushare_client

    settings = get_settings()
    print(f"数据源：{settings.news_sources}")
    print(f"LLM 凭据：{'已配置' if settings.has_llm_credentials() else '未配置（分析链路将跳过）'}")

    try:
        client = get_tushare_client(settings)
    except ValueError as exc:
        print(f"Tushare 自检失败：{exc}")
        return 1

    from datetime import timedelta

    from fin_news.core.timeutil import now
    from fin_news.ingestion.sources.tushare_news import TushareNewsSource

    ok = True
    for src in settings.news_sources:
        source = TushareNewsSource(src, client=client, settings=settings)
        try:
            items = await source.fetch(now() - timedelta(hours=3), now())
            print(f"  {src}: OK, 近 3 小时 {len(items)} 条")
            if items:
                sample = items[0]
                print(f"    样例标题：{sample.title}")
        except TusharePermissionError as exc:
            ok = False
            print(f"  {src}: 无权限 -> {exc}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  {src}: 失败 -> {str(exc)[:200]}")
    return 0 if ok else 1


async def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "ingest":
        return await _cmd_ingest()
    if args.command == "pipeline":
        return await _cmd_pipeline(once=True)
    if args.command == "worker":
        return await _cmd_pipeline(once=False)
    if args.command == "score":
        return await _cmd_score()
    if args.command == "premarket":
        return await _cmd_market("pre")
    if args.command == "postmarket":
        return await _cmd_market("post")
    if args.command == "status":
        return await _cmd_status()
    if args.command == "selftest":
        return await _cmd_selftest()
    print(__doc__)
    return 1


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="fin-news-v5 命令行工具")
    parser.add_argument(
        "command",
        choices=[
            "ingest",
            "pipeline",
            "worker",
            "score",
            "premarket",
            "postmarket",
            "status",
            "selftest",
        ],
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_dispatch(args)))


if __name__ == "__main__":
    main()
