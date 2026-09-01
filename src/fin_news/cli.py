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

logger = get_logger("cli")


async def _cmd_ingest() -> int:
    from fin_news.core.db import init_db
    from fin_news.ingestion.service import IngestionService

    await init_db()
    results = await IngestionService().run_all()
    for r in results:
        print(
            f"[{r.status:8}] {r.source_key}: fetched={r.fetched} "
            + f"inserted={r.inserted} duplicates={r.duplicates} filtered={r.filtered}"
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


async def _cmd_embed(limit: int | None = None) -> int:
    """直接向量化已评分的资讯，不依赖事件队列。

    用途：补数、事件已 DONE 但资讯未向量化、或只想验证 embedding 链路。
    """
    from sqlalchemy import select

    from fin_news.agents.embeddings import DimensionMismatch
    from fin_news.core.db import init_db, session_scope
    from fin_news.core.enums import EventType, NewsStatus
    from fin_news.core.logging import get_logger
    from fin_news.domain.scoring import should_vectorize
    from fin_news.events.bus import EventBus
    from fin_news.models.news import NewsItem
    from fin_news.pipeline.handlers.on_scored import vectorize_news

    settings = get_settings()
    if not settings.has_llm_credentials():
        print("未配置模型 API Key，无法向量化")
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
            print("没有待向量化的资讯（要求 status=SCORED/EMBED_FAILED 且 score 非空）")
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
                print(f"向量维度不匹配，终止：{exc}")
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

    get_logger("cli.embed").info(
        "向量化完成", embedded=embedded, skipped=skipped, failed=failed
    )
    print(f"已向量化：{embedded}，归档为噪声：{skipped}，失败：{failed}")
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
    from fin_news.core.logging import get_logger
    from fin_news.domain.scoring import should_vectorize
    from fin_news.events.bus import EventBus
    from fin_news.models.event import IngestEvent
    from fin_news.models.news import NewsChunk, NewsItem

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

    print(
        f"扫描结果：噪声待归档 {len(to_archive)}，"
        f"缺评分事件 {len(to_publish)}，分块缺失待重跑 {len(to_revectorize)}"
    )
    if not apply:
        print("（dry-run，加 --apply 才会实际修改）")
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

    get_logger("cli.sweep").info(
        "状态修正完成", archived=len(to_archive), published=len(to_publish),
        revectorize=len(to_revectorize),
    )
    print(f"已修正：{fixed} 项")
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
                + f"上次条数={c.last_count} enabled={c.enabled}"
            )
    return 0


async def _cmd_selftest() -> int:
    """数据源 / LLM / Embedding 连通性自检。"""
    settings = get_settings()
    print(f"数据源：{settings.news_sources}")
    print(f"LLM 凭据：{'已配置' if settings.has_llm_credentials() else '未配置（分析链路将跳过）'}")

    source_ok = await _selftest_sources(settings)
    llm_ok = await _selftest_llm(settings)
    embed_ok = await _selftest_embedding(settings)

    all_ok = source_ok and llm_ok and embed_ok
    print("\n自检结果：" + ("全部通过" if all_ok else "存在问题，见上方 [FAIL]/[WARN] 项"))
    return 0 if all_ok else 1


async def _selftest_sources(settings: Settings) -> bool:
    """Tushare 资讯源连通性。"""
    from datetime import timedelta

    from fin_news.core.timeutil import now
    from fin_news.ingestion.sources.tushare_news import TushareNewsSource
    from fin_news.ingestion.tushare_client import TusharePermissionError, get_tushare_client

    print("\n数据源自检：")
    try:
        client = get_tushare_client(settings)
    except ValueError as exc:
        print(f"  [FAIL] Tushare 客户端初始化失败：{exc}")
        return False

    ok = True
    for src in settings.news_sources:
        source = TushareNewsSource(src, client=client, settings=settings)
        try:
            items = await source.fetch(now() - timedelta(hours=3), now())
            print(f"  [OK] {src}: 近 3 小时 {len(items)} 条")
            if items:
                sample = items[0]
                print(f"       样例标题：{sample.title or '(无标题，接入时会兜底)'}")
        except TusharePermissionError as exc:
            ok = False
            print(f"  [FAIL] {src}: 无权限 -> {exc}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  [FAIL] {src}: {type(exc).__name__} -> {str(exc)[:200]}")
    return ok


async def _selftest_llm(settings: Settings) -> bool:
    """逐个角色做一次最小调用，验证模型可用 + JSON 结构化输出 + 是否走了降级。"""
    from fin_news.agents.llm.client import LLMUnavailable, get_llm_client

    print("\nLLM 自检（每个角色一次最小调用，验证 JSON 结构化输出）：")
    if not settings.has_llm_credentials():
        print("  [SKIP] 未配置任何模型 API Key，评分 / 分析 / 追问链路会跳过")
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
            print(f"  [FAIL] {role}: 主备模型均不可用 -> {str(exc)[:200]}")
            continue
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  [FAIL] {role}: {type(exc).__name__} -> {str(exc)[:200]}")
            continue

        structured = resp.data is not None
        if resp.is_fallback:
            degraded_roles.append(role)
        flag = "OK" if structured else "WARN"
        if not structured:
            ok = False
        print(
            f"  [{flag}] {role}: provider={resp.provider} model={resp.model}"
            + (" (降级到备 provider)" if resp.is_fallback else "")
            + f" 延迟={resp.latency_ms}ms tokens={resp.prompt_tokens}+{resp.completion_tokens}"
            + f" 结构化输出={'正常' if structured else '解析失败 -> ' + (resp.content or '')[:60]}"
        )

    if degraded_roles:
        # 主 provider 模型不可用（常见原因：模型名不存在 / 无权限），全链路都在走备 provider
        roles = ", ".join(degraded_roles)
        provider_name = settings.llm_default_provider
        print(
            f"  [WARN] {roles} 走了备 provider，"
            + f"请检查 {provider_name} 的模型名是否为账号下真实存在的模型 ID"
        )
    return ok


async def _selftest_embedding(settings: Settings) -> bool:
    """验证 embedding 可用、维度与配置一致、且与数据库列类型 / 索引兼容。"""
    import time

    from fin_news.agents.embeddings import DimensionMismatch, Embedder

    print("\nEmbedding 自检：")
    provider = settings.embedding_provider
    model = settings.model_for(provider, "embedding")  # type: ignore[arg-type]
    cfg = settings.provider(provider)  # type: ignore[arg-type]
    if not cfg.api_key:
        print(f"  [SKIP] {provider} 未配置 api_key，score>3 的资讯无法向量化（检索与历史分析会失效）")
        return True

    embedder = Embedder(settings)
    started = time.perf_counter()
    try:
        vec = await embedder.embed_one("央行宣布下调存款准备金率 0.5 个百分点")
    except DimensionMismatch as exc:
        real_dim = await _probe_embedding_dim(embedder)
        print(f"  [FAIL] {exc}")
        if real_dim:
            print(f"         模型 {model} 实际输出 {real_dim} 维 -> 请设置 EMBEDDING_DIM={real_dim} 并执行迁移")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] 调用失败：{type(exc).__name__} -> {str(exc)[:200]}")
        return False

    latency_ms = int((time.perf_counter() - started) * 1000)
    actual_dim = len(vec)
    print(f"  [OK] provider={provider} model={model} dim={actual_dim} 延迟={latency_ms}ms")

    # 与数据库列类型 / 维度 / 索引一致性校验（这三项不一致会在入库或建索引时才炸）
    return await _check_vector_column(vec)


async def _probe_embedding_dim(embedder: Embedder) -> int | None:
    """绕过维度校验，取模型真实输出维度（用于报错时给出正确的 EMBEDDING_DIM 取值）。"""
    try:
        settings = embedder.settings
        resp = await embedder.client.embeddings.create(
            model=settings.model_for(settings.embedding_provider, "embedding"),
            input=["维度探测"],
        )
        return len(resp.data[0].embedding)
    except Exception:  # noqa: BLE001
        return None


async def _check_vector_column(vec: list[float]) -> bool:  # noqa: C901
    """校验 news_chunk.embedding 的列类型、维度、索引与实际向量是否匹配。

    同时做一次「写入 + 相似度检索」探针（放在事务里，最后回滚，不留脏数据）。
    """
    import re

    from sqlalchemy import text

    from fin_news.core.db import get_session_factory, init_db

    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] 数据库不可用，跳过向量列校验：{str(exc)[:150]}")
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
        print("  [WARN] 未找到 news_chunk.embedding 列，请先执行 alembic upgrade head")
        return True

    m = re.match(r"^(halfvec|vector)\((\d+)\)$", str(col_type))
    if not m:
        print(f"  [FAIL] 列类型异常：{col_type}（应为 vector(n) 或 halfvec(n)）")
        return False
    type_name, col_dim = m.group(1), int(m.group(2))
    actual_dim = len(vec)
    print(f"  数据库列：news_chunk.embedding = {col_type}")

    if col_dim != actual_dim:
        ok = False
        print(
            f"  [FAIL] 列维度 {col_dim} != 模型输出 {actual_dim}："
            + f"需改列类型并重建索引（EMBEDDING_DIM={actual_dim}）"
        )
    # pgvector 索引上限：float vector 最多 2000 维，halfvec 可到 4000 维
    if type_name == "vector" and actual_dim > 2000:
        ok = False
        print(
            f"  [FAIL] {actual_dim} 维超过 float vector 的索引上限（2000）："
            + f"列类型需改为 halfvec({actual_dim})，索引算子改为 halfvec_cosine_ops"
        )
    if indexdef:
        expect_ops = f"{type_name}_cosine_ops"
        if "hnsw" not in indexdef:
            print(f"  [WARN] 未使用 HNSW 索引：{indexdef}")
        elif expect_ops not in indexdef:
            ok = False
            print(f"  [FAIL] 索引算子与列类型不匹配，应为 {expect_ops}：{indexdef}")
        else:
            print(f"  [OK] 索引算子匹配：{expect_ops}")
    else:
        print("  [WARN] 未找到 idx_chunk_embedding 索引")

    # 端到端探针：写入 + 检索（事务回滚，不落库）
    async with factory() as session:
        try:
            news_id = (
                await session.execute(text("SELECT id FROM news_item ORDER BY id LIMIT 1"))
            ).scalar()
            if news_id is None:
                print("  [SKIP] 库内暂无资讯，跳过写入+检索探针")
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
            print(
                f"  [{'OK' if probe_ok else 'FAIL'}] 写入+检索探针：自身相似度={sim_f:.4f}"
                + "（预期 1.0，事务已回滚）"
            )
            if not probe_ok:
                ok = False
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  [FAIL] 写入+检索探针失败：{type(exc).__name__} -> {str(exc)[:200]}")
        finally:
            await session.rollback()
    return ok


async def _dispatch(args: argparse.Namespace) -> int:
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
    args = parser.parse_args()
    sys.exit(asyncio.run(_dispatch(args)))


if __name__ == "__main__":
    main()
