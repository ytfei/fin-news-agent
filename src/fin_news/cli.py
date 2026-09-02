"""命令行工具（运维与本地调试）。

用法：
    uv run python -m fin_news.cli ingest           # 手动跑一次增量接入
    uv run python -m fin_news.cli pipeline         # 消费事件（跑一轮）
    uv run python -m fin_news.cli worker           # 常驻 worker
    uv run python -m fin_news.cli score            # 给待评分资讯打分（并补发下游事件）
    uv run python -m fin_news.cli embed            # 直接向量化已评分资讯（不依赖事件队列）
    uv run python -m fin_news.cli embed --limit 20
    uv run python -m fin_news.cli sweep            # 扫描状态与事件不一致（dry-run）
    uv run python -m fin_news.cli sweep --apply    # 实际修正
    uv run python -m fin_news.cli premarket        # 生成盘前简报
    uv run python -m fin_news.cli postmarket       # 生成盘后简报
    uv run python -m fin_news.cli status           # 查看积压与统计
    uv run python -m fin_news.cli selftest         # 数据源 / LLM / Embedding 连通性自检

日志级别：默认 INFO；`-v` 升到 DEBUG（看业务 debug 日志）；`-vv` 再把
第三方库（httpx/openai/sqlalchemy 等）的日志也放开到 DEBUG。

执行路径追踪：`pipeline` / `worker` 每次运行会生成 `run_id` 并绑定到日志上下文，
cli → worker → handler → agent 的每一环都会输出「开始 / 结束（含 elapsed_ms）/ 异常」，
按 run_id 过滤即可还原一次运行的完整执行路径。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING

from fin_news.core.config import Settings, get_settings
from fin_news.core.logging import configure_logging, get_logger

if TYPE_CHECKING:
    from fin_news.agents.embeddings import Embedder
    from fin_news.pipeline.worker import PipelineWorker

# 不在模块级调用 get_logger —— 那会在 import 时就用默认级别完成日志配置，导致
# -v/-vv 无法生效。日志级别统一由 main() 解析参数后配置，各命令函数内再 get_logger
# 拿到的即是按 verbosity 配置好的 logger。
_LOG_NAME = "cli"


async def _cmd_ingest() -> int:
    from fin_news.core.db import init_db
    from fin_news.ingestion.service import IngestionService

    logger = get_logger(_LOG_NAME)
    await init_db()
    results = await IngestionService().run_all()
    for r in results:
        logger.info(
            "接入结果",
            source_key=r.source_key,
            status=r.status,
            fetched=r.fetched,
            inserted=r.inserted,
            duplicates=r.duplicates,
            filtered=r.filtered,
        )
    return 0


async def _cmd_pipeline(once: bool = True) -> int:
    import time
    import uuid

    from fin_news.core.db import init_db
    from fin_news.core.logging import bind_context, elapsed_ms, stage
    from fin_news.pipeline.worker import PipelineWorker

    logger = get_logger(_LOG_NAME)
    settings = get_settings()
    command = "pipeline" if once else "worker"

    # 一次运行的唯一标识：贯穿 cli → worker → handler → agent 的所有日志
    run_id = uuid.uuid4().hex[:12]
    bind_context(run_id=run_id, command=command)

    started = time.perf_counter()
    logger.info(
        "Pipeline 运行开始",
        mode="单轮" if once else "常驻",
        worker_batch_limit=settings.worker_batch_limit,
        worker_poll_interval_seconds=settings.worker_poll_interval_seconds,
        scoring_batch_size=settings.scoring_batch_size,
        scoring_window_seconds=settings.scoring_window_seconds,
        agent_framework=settings.agent_framework,
        use_deep_agents=settings.use_deep_agents,
        llm_provider=settings.llm_default_provider,
        llm_configured=settings.has_llm_credentials(),
        embedding_provider=settings.embedding_provider,
        score_threshold_vectorize=settings.score_threshold_vectorize,
    )

    worker = PipelineWorker()
    bind_context(worker_id=worker.worker_id)
    status = "完成"
    exit_code = 0
    try:
        async with stage(logger, "初始化数据库"):
            await init_db()

        async with stage(logger, "回收超时事件"):
            await worker.reclaim()

        if once:
            async with stage(logger, "消费事件") as out:
                n = await worker.tick()
                out["processed"] = n
            # 攒批未满的 news.ingested 事件仍在攒批器里（PROCESSING 态），
            # 单轮模式下必须放回队列，否则会卡到 reclaim 超时
            async with stage(logger, "回写攒批事件") as out:
                out["released"] = await worker.flush()
        else:
            # 常驻模式：启动 / 停止 / 每轮统计由 worker 自己打印（含 worker_id）
            _install_stop_handler(worker)
            await worker.run_forever()
    except Exception:  # noqa: BLE001 - 结束日志必须打出来，异常统一在这里收口
        status = "异常"
        exit_code = 1
        logger.exception("Pipeline 运行异常")
    finally:
        logger.info(
            "Pipeline 运行结束",
            status=status,
            elapsed_ms=elapsed_ms(started),
            **worker.stats,
        )
    return exit_code


def _install_stop_handler(worker: PipelineWorker) -> None:
    """SIGINT / SIGTERM 触发优雅退出：跑完当前批次 → flush 攒批 → 打印结束日志。

    信号回调只置位，不打断进行中的批次；不支持信号回调的平台（如 Windows）
    忽略即可，退回默认行为。
    """
    import signal

    logger = get_logger(_LOG_NAME)

    def _stop(signame: str) -> None:
        logger.info("收到停止信号，开始优雅退出", signal=signame)
        worker.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _stop, sig.name)
        except (NotImplementedError, RuntimeError, ValueError):
            logger.debug("当前平台不支持信号回调，忽略优雅退出", signal=sig.name)


async def _cmd_score() -> int:
    from fin_news.agents.scoring_agent import ScoringAgent
    from fin_news.core.db import init_db, session_scope

    logger = get_logger(_LOG_NAME)
    await init_db()
    async with session_scope() as session:
        n = await ScoringAgent().score_pending(session)
        logger.info("已评分", count=n)
    return 0


async def _cmd_embed(limit: int | None = None) -> int:
    """直接向量化已评分的资讯，不依赖事件队列。

    用途：补数、事件已 DONE 但资讯未向量化、或只想验证 embedding 链路。
    """
    from sqlalchemy import select

    from fin_news.agents.embeddings import DimensionMismatch
    from fin_news.core.db import init_db, session_scope
    from fin_news.core.enums import EventType, NewsStatus
    from fin_news.domain.scoring import should_vectorize
    from fin_news.events.bus import EventBus
    from fin_news.models.news import NewsItem
    from fin_news.pipeline.handlers.on_scored import vectorize_news

    logger = get_logger(_LOG_NAME)
    settings = get_settings()
    if not settings.has_llm_credentials():
        logger.warning("未配置模型 API Key，无法向量化")
        return 1

    await init_db()
    batch = limit or settings.scoring_batch_size * 10
    embedded = skipped = failed = 0

    async with session_scope() as session:
        rows = await session.execute(
            select(NewsItem)
            .where(
                NewsItem.status.in_([NewsStatus.SCORED, NewsStatus.EMBED_FAILED]),
                NewsItem.score.is_not(None),
            )
            .order_by(NewsItem.publish_time)
            .limit(batch)
        )
        items = list(rows.scalars().all())
        if not items:
            logger.info("没有待向量化的资讯（要求 status=SCORED/EMBED_FAILED 且 score 非空）")
            return 0

        bus = EventBus(session, worker_id="cli-embed")
        for news in items:
            if not should_vectorize(news.score, settings.score_threshold_vectorize):
                news.status = NewsStatus.ARCHIVED_NOISE
                news.analysis_status = "NONE"
                skipped += 1
                continue

            try:
                # 用 SAVEPOINT 隔离单条失败，避免脏写入留在事务里
                async with session.begin_nested():
                    chunks = await vectorize_news(session, news, settings)
            except DimensionMismatch as exc:
                logger.error("向量维度不匹配，终止", error=str(exc))
                return 1
            except Exception as exc:  # noqa: BLE001
                news.status = NewsStatus.EMBED_FAILED
                news.retry_count = (news.retry_count or 0) + 1
                news.last_error = str(exc)[:500]
                failed += 1
                continue

            news.status = NewsStatus.EMBEDDED
            news.analysis_status = "PENDING"
            news.last_error = None
            logger.info("已向量化", news_id=news.id, chunks=chunks, score=news.score)
            await bus.publish(
                EventType.NEWS_EMBEDDED,
                news.id,
                payload={"score": news.score, "band": news.band.value if news.band else None},
                priority=2,
            )
            embedded += 1
            # 逐条提交：量大时已完成的结果立即可见
            await session.commit()

    logger.info("向量化完成", embedded=embedded, skipped=skipped, failed=failed)
    return 0 if failed == 0 else 1


async def _cmd_sweep(apply: bool = False) -> int:
    """扫描并修正「资讯状态」与「事件」之间的不一致。

    三类问题：
    1. status=SCORED 但 score<=3  → 应为 ARCHIVED_NOISE（不该再向量化）
    2. status=SCORED 且 score>3 但没有 news.scored 事件 → 补发（否则永远不向量化）
    3. status=EMBEDDED 但没有分块 → 打回 SCORED 并补发事件，重新向量化
    """
    from sqlalchemy import select

    from fin_news.core.db import init_db, session_scope
    from fin_news.core.enums import EventStatus, EventType, NewsStatus
    from fin_news.domain.scoring import should_vectorize
    from fin_news.events.bus import EventBus
    from fin_news.models.event import IngestEvent
    from fin_news.models.news import NewsChunk, NewsItem

    logger = get_logger(_LOG_NAME)
    settings = get_settings()
    await init_db()

    to_archive: list[int] = []
    to_publish: list[tuple[int, int]] = []  # (news_id, score)
    to_revectorize: list[int] = []

    async with session_scope() as session:
        rows = await session.execute(
            select(NewsItem).where(
                NewsItem.status.in_(
                    [NewsStatus.SCORED, NewsStatus.EMBED_FAILED, NewsStatus.EMBEDDED]
                )
            )
        )
        items = list(rows.scalars().all())

        scored_events = {
            r[0]
            for r in (
                await session.execute(
                    select(IngestEvent.aggregate_id).where(
                        IngestEvent.event_type == EventType.NEWS_SCORED.value,
                        IngestEvent.status.in_(
                            [EventStatus.PENDING, EventStatus.PROCESSING, EventStatus.DONE]
                        ),
                    )
                )
            ).all()
        }
        chunked = {
            r[0]
            for r in (
                await session.execute(
                    select(NewsChunk.news_id).group_by(NewsChunk.news_id)
                )
            ).all()
        }

        for news in items:
            if news.status in (NewsStatus.SCORED, NewsStatus.EMBED_FAILED):
                if news.score is None:
                    continue
                if not should_vectorize(news.score, settings.score_threshold_vectorize):
                    to_archive.append(news.id)
                elif news.id not in scored_events:
                    to_publish.append((news.id, news.score))
            elif news.status == NewsStatus.EMBEDDED and news.id not in chunked:
                to_revectorize.append(news.id)

    logger.info(
        "扫描结果",
        archive=len(to_archive),
        publish=len(to_publish),
        revectorize=len(to_revectorize),
    )
    if not apply:
        logger.info("dry-run 模式，未做修改（加 --apply 才会实际执行）")
        return 0

    fixed = 0
    async with session_scope() as session:
        bus = EventBus(session, worker_id="cli-sweep")
        if to_archive:
            rows = await session.execute(
                select(NewsItem).where(NewsItem.id.in_(to_archive))
            )
            for news in rows.scalars().all():
                news.status = NewsStatus.ARCHIVED_NOISE
                news.analysis_status = "NONE"
                fixed += 1
        for news_id, score in to_publish:
            if await bus.publish(
                EventType.NEWS_SCORED, news_id, payload={"score": score}, priority=2
            ):
                fixed += 1
        for news_id in to_revectorize:
            rows = await session.execute(select(NewsItem).where(NewsItem.id == news_id))
            news = rows.scalar_one_or_none()
            if news is not None:
                news.status = NewsStatus.SCORED
                news.analysis_status = "PENDING"
            if await bus.publish(
                EventType.NEWS_SCORED, news_id, payload={"sweep": True}, priority=2
            ):
                fixed += 1

    logger.info("状态修正完成", fixed=fixed)
    return 0


async def _cmd_market(period: str) -> int:
    from fin_news.agents.market_agents import run_post_market, run_pre_market
    from fin_news.core.db import init_db

    logger = get_logger(_LOG_NAME)
    await init_db()
    report = await run_pre_market() if period == "pre" else await run_post_market()
    if report is None:
        logger.warning("未生成简报（非交易日或未配置模型 Key）", period=period)
        return 1
    logger.info("简报已生成", period=period, report_id=report.id, title=report.title)
    return 0


async def _cmd_status() -> int:
    from sqlalchemy import func, select

    from fin_news.core.db import init_db, session_scope
    from fin_news.events.bus import EventBus
    from fin_news.models.analysis import IngestCursor
    from fin_news.models.news import NewsItem

    logger = get_logger(_LOG_NAME)
    await init_db()
    async with session_scope() as session:
        backlog = await EventBus(session).backlog()
        total = await session.scalar(select(func.count()).select_from(NewsItem))
        scored = await session.scalar(
            select(func.count()).select_from(NewsItem).where(NewsItem.score.is_not(None))
        )
        logger.info("统计", backlog=backlog, total=total, scored=scored)
        rows = (await session.execute(select(IngestCursor))).scalars().all()
        for c in rows:
            logger.info(
                "接入位点",
                source_key=c.source_key,
                cursor_time=str(c.cursor_time),
                last_status=c.last_status,
                last_count=c.last_count,
                enabled=c.enabled,
            )
    return 0


async def _cmd_selftest() -> int:
    """数据源 / LLM / Embedding 连通性自检。"""
    logger = get_logger(_LOG_NAME)
    settings = get_settings()
    logger.info(
        "自检开始",
        sources=settings.news_sources,
        llm_configured=settings.has_llm_credentials(),
    )

    source_ok = await _selftest_sources(settings)
    llm_ok = await _selftest_llm(settings)
    embed_ok = await _selftest_embedding(settings)

    all_ok = source_ok and llm_ok and embed_ok
    logger.info(
        "自检结果",
        result="全部通过" if all_ok else "存在问题",
        sources=source_ok,
        llm=llm_ok,
        embedding=embed_ok,
    )
    return 0 if all_ok else 1


async def _selftest_sources(settings: Settings) -> bool:
    """Tushare 资讯源连通性。"""
    from datetime import timedelta

    from fin_news.core.timeutil import now
    from fin_news.ingestion.sources.tushare_news import TushareNewsSource
    from fin_news.ingestion.tushare_client import TusharePermissionError, get_tushare_client

    logger = get_logger(_LOG_NAME)
    try:
        client = get_tushare_client(settings)
    except ValueError as exc:
        logger.error("数据源自检失败", detail=f"Tushare 客户端初始化失败：{exc}")
        return False

    ok = True
    for src in settings.news_sources:
        source = TushareNewsSource(src, client=client, settings=settings)
        try:
            items = await source.fetch(now() - timedelta(hours=3), now())
            logger.info("数据源自检通过", source=src, count=len(items))
            if items:
                sample = items[0]
                logger.info("数据源样例", source=src, title=sample.title or "(无标题，接入时会兜底)")
        except TusharePermissionError as exc:
            ok = False
            logger.error("数据源自检失败", source=src, detail=f"无权限 -> {exc}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            logger.error(
                "数据源自检失败", source=src, detail=f"{type(exc).__name__} -> {str(exc)[:200]}"
            )
    return ok


async def _selftest_llm(settings: Settings) -> bool:
    """逐个角色做一次最小调用，验证模型可用 + JSON 结构化输出 + 是否走了降级。"""
    from fin_news.agents.llm.client import LLMUnavailable, get_llm_client

    logger = get_logger(_LOG_NAME)
    if not settings.has_llm_credentials():
        logger.warning("LLM 自检跳过", detail="未配置任何模型 API Key，评分 / 分析 / 追问链路会跳过")
        return True

    client = get_llm_client(settings)
    ok = True
    degraded_roles: list[str] = []

    for role in ("scoring", "analysis", "qa"):
        try:
            resp = await client.chat(
                role=role,  # type: ignore[arg-type]
                system="你是连通性自检程序。",
                user='只输出 JSON：{"ok": true}',
                json_mode=True,
                temperature=0,
                max_tokens=32,
            )
        except LLMUnavailable as exc:
            ok = False
            logger.error("LLM 自检失败", role=role, detail=f"主备模型均不可用 -> {str(exc)[:200]}")
            continue
        except Exception as exc:  # noqa: BLE001
            ok = False
            logger.error("LLM 自检失败", role=role, detail=f"{type(exc).__name__} -> {str(exc)[:200]}")
            continue

        structured = resp.data is not None
        if resp.is_fallback:
            degraded_roles.append(role)
        if not structured:
            ok = False
        fields = dict(
            role=role,
            provider=resp.provider,
            model=resp.model,
            fallback=resp.is_fallback,
            latency_ms=resp.latency_ms,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            structured=structured,
        )
        if structured:
            logger.info("LLM 自检通过", **fields)
        else:
            logger.warning("LLM 自检（结构化输出异常）", **fields)

    if degraded_roles:
        logger.warning(
            "有角色走了备 provider",
            roles=", ".join(degraded_roles),
            detail=f"请检查 {settings.llm_default_provider} 的模型名是否为账号下真实存在的模型 ID",
        )
    return ok


async def _selftest_embedding(settings: Settings) -> bool:
    """验证 embedding 可用、维度与配置一致、且与数据库列类型 / 索引兼容。"""
    import time

    from fin_news.agents.embeddings import DimensionMismatch, Embedder

    logger = get_logger(_LOG_NAME)
    provider = settings.embedding_provider
    model = settings.model_for(provider, "embedding")  # type: ignore[arg-type]
    cfg = settings.provider(provider)  # type: ignore[arg-type]
    if not cfg.api_key:
        logger.warning("Embedding 自检跳过", detail=f"{provider} 未配置 api_key，score>3 的资讯无法向量化")
        return True

    embedder = Embedder(settings)
    started = time.perf_counter()
    try:
        vec = await embedder.embed_one("央行宣布下调存款准备金率 0.5 个百分点")
    except DimensionMismatch as exc:
        real_dim = await _probe_embedding_dim(embedder)
        logger.error(
            "Embedding 自检失败",
            detail=str(exc),
            expected_dim=settings.embedding_dim,
            real_dim=real_dim,
        )
        if real_dim:
            logger.error(
                "维度不匹配",
                model=model,
                real_dim=real_dim,
                hint=f"请设置 EMBEDDING_DIM={real_dim} 并执行迁移",
            )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.error("Embedding 自检失败", detail=f"{type(exc).__name__} -> {str(exc)[:200]}")
        return False

    latency_ms = int((time.perf_counter() - started) * 1000)
    actual_dim = len(vec)
    logger.info(
        "Embedding 自检通过",
        provider=provider,
        model=model,
        dim=actual_dim,
        latency_ms=latency_ms,
    )

    # 与数据库列类型 / 维度 / 索引一致性校验（这三项不一致会在入库或建索引时才炸）
    return await _check_vector_column(vec)


async def _probe_embedding_dim(embedder: Embedder) -> int | None:
    """取模型真实输出维度（用于报错时给出正确的 EMBEDDING_DIM 取值）。

    doubao-embedding-vision 的向量维度由请求参数 `dimensions` 决定（1024/2048），
    返回维度恒等于请求维度，因此直接返回配置值即可——不再像旧文本模型那样
    （doubao-embedding-text-240715 固定 2560 维）需要真实调用探测。
    """
    return embedder.settings.embedding_dim


async def _check_vector_column(vec: list[float]) -> bool:  # noqa: C901
    """校验 news_chunk.embedding 的列类型、维度、索引与实际向量是否匹配。

    同时做一次「写入 + 相似度检索」探针（放在事务里，最后回滚，不留脏数据）。
    """
    import re

    from sqlalchemy import text

    from fin_news.core.db import get_session_factory, init_db

    logger = get_logger(_LOG_NAME)
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("数据库不可用，跳过向量列校验", detail=str(exc)[:150])
        return True

    ok = True
    factory = get_session_factory()

    async with factory() as session:
        col_type = (
            await session.execute(
                text(
                    "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                    + "WHERE a.attrelid = 'news_chunk'::regclass AND a.attname = 'embedding' AND a.attnum > 0"
                )
            )
        ).scalar()
        indexdef = (
            await session.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_chunk_embedding'")
            )
        ).scalar() or ""

    if not col_type:
        logger.warning("未找到 news_chunk.embedding 列", detail="请先执行 alembic upgrade head")
        return True

    m = re.match(r"^(halfvec|vector)\((\d+)\)$", str(col_type))
    if not m:
        logger.error("列类型异常", col_type=str(col_type), detail="应为 vector(n) 或 halfvec(n)")
        return False
    type_name, col_dim = m.group(1), int(m.group(2))
    actual_dim = len(vec)
    logger.info("数据库向量列", column=f"news_chunk.embedding = {col_type}")

    if col_dim != actual_dim:
        ok = False
        logger.error(
            "列维度不匹配",
            col_dim=col_dim,
            actual_dim=actual_dim,
            hint=f"需改列类型并重建索引（EMBEDDING_DIM={actual_dim}）",
        )
    # pgvector 索引上限：float vector 最多 2000 维，halfvec 可到 4000 维
    if type_name == "vector" and actual_dim > 2000:
        ok = False
        logger.error(
            "float vector 维度过高",
            actual_dim=actual_dim,
            hint=f"列类型需改为 halfvec({actual_dim})，索引算子改为 halfvec_cosine_ops",
        )
    if indexdef:
        expect_ops = f"{type_name}_cosine_ops"
        if "hnsw" not in indexdef:
            logger.warning("未使用 HNSW 索引", indexdef=indexdef)
        elif expect_ops not in indexdef:
            ok = False
            logger.error("索引算子不匹配", expected=expect_ops, indexdef=indexdef)
        else:
            logger.info("索引算子匹配", ops=expect_ops)
    else:
        logger.warning("未找到 idx_chunk_embedding 索引")

    # 端到端探针：写入 + 检索（事务回滚，不落库）
    async with factory() as session:
        try:
            news_id = (
                await session.execute(text("SELECT id FROM news_item ORDER BY id LIMIT 1"))
            ).scalar()
            if news_id is None:
                logger.warning("库内暂无资讯，跳过写入+检索探针")
                return ok
            literal = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
            await session.execute(
                text(
                    "INSERT INTO news_chunk (news_id, chunk_index, content, embedding, model) "
                    + f"VALUES (:news_id, 999999, 'selftest-probe', CAST(:vec AS {type_name}), 'selftest') "
                    + "ON CONFLICT (news_id, chunk_index) DO NOTHING"
                ),
                {"news_id": news_id, "vec": literal},
            )
            sim = (
                await session.execute(
                    text(
                        "SELECT 1 - (embedding <=> CAST(:vec AS " + type_name + ")) "
                        + "FROM news_chunk WHERE chunk_index = 999999 AND model = 'selftest'"
                    ),
                    {"vec": literal},
                )
            ).scalar()
            sim_f = float(sim) if sim is not None else float("nan")
            probe_ok = abs(sim_f - 1.0) < 1e-3
            if probe_ok:
                logger.info("写入+检索探针通过", similarity=round(sim_f, 4))
            else:
                logger.error("写入+检索探针失败", similarity=round(sim_f, 4), expected=1.0)
                ok = False
        except Exception as exc:  # noqa: BLE001
            ok = False
            logger.error("写入+检索探针异常", detail=f"{type(exc).__name__} -> {str(exc)[:200]}")
        finally:
            await session.rollback()
    return ok


async def _dispatch(args: argparse.Namespace) -> int:
    logger = get_logger(_LOG_NAME)
    if args.command == "ingest":
        return await _cmd_ingest()
    if args.command == "pipeline":
        return await _cmd_pipeline(once=True)
    if args.command == "worker":
        return await _cmd_pipeline(once=False)
    if args.command == "score":
        return await _cmd_score()
    if args.command == "embed":
        return await _cmd_embed(limit=getattr(args, "limit", None))
    if args.command == "sweep":
        return await _cmd_sweep(apply=getattr(args, "apply", False))
    if args.command == "premarket":
        return await _cmd_market("pre")
    if args.command == "postmarket":
        return await _cmd_market("post")
    if args.command == "status":
        return await _cmd_status()
    if args.command == "selftest":
        return await _cmd_selftest()
    logger.info("未知命令，打印用法", command=args.command)
    logger.info(__doc__)
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="fin-news-v5 命令行工具")
    parser.add_argument(
        "command",
        choices=[
            "ingest",
            "pipeline",
            "worker",
            "score",
            "embed",
            "sweep",
            "premarket",
            "postmarket",
            "status",
            "selftest",
        ],
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="embed：本轮最多处理的资讯条数"
    )
    parser.add_argument(
        "--apply", action="store_true", help="sweep：实际执行修正（默认只扫描不修改）"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="增加日志详细程度：-v=DEBUG，-vv=DEBUG 并放开第三方库日志",
    )
    args = parser.parse_args()

    # 先按 verbosity 配置日志，再执行命令（保证 -v/-vv 对命令内日志生效）
    configure_logging(verbosity=getattr(args, "verbose", 0) or 0)
    sys.exit(asyncio.run(_dispatch(args)))


if __name__ == "__main__":
    main()
