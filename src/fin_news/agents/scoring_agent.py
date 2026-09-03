"""评分 Agent：小批并发调用轻量 flash 模型对资讯打分（1-10）。

性能策略：整批资讯（默认 30 条）按 `scoring_sub_batch_size`（默认 10）切成
小子批，经 `scoring_concurrency` 信号量限流并发执行 LangGraph 评分（失败回退
该子批 legacy），各子批保留独立的 rescue / 退化护栏，最后按原始顺序合并结果，
统一落库与补发下游事件。

相比「整批 30 条一次大 JSON 生成」：
* 单批墙钟从单次大调用时长降为最大子批耗时（默认 3 个子批全并行）；
* 输出条数少，生成更稳、更不易超时，漏评只补打对应子批。
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.graphs.scoring_graph import ScoringRun, build_payload, run_scoring
from fin_news.agents.llm import get_llm_client, get_semaphore
from fin_news.agents.prompts import SCORING_SCHEMA, SCORING_SYSTEM, SCORING_VERSION
from fin_news.agents.registry import get_agent
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import AgentType, EntityType, EventType, NewsStatus
from fin_news.core.logging import get_logger, stage
from fin_news.core.timeutil import now_utc
from fin_news.domain.schemas import ScoreBatchResult, ScoreEntity, ScoreItemResult
from fin_news.domain.scoring import band_for_score, clamp_score
from fin_news.events.bus import EventBus
from fin_news.models.news import NewsEntity, NewsItem, NewsScore

logger = get_logger("agents.scoring")


class ScoringAgent:
    """批量评分。失败按文档约定：整批重试 → 小批重试 → 标记 SCORE_FAILED。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------
    async def score_pending(self, session: AsyncSession, limit: int | None = None) -> int:
        """扫描 NEW 状态的资讯并批量评分，返回已处理条数。"""
        batch_size = limit or self.settings.scoring_batch_size
        rows = await session.execute(
            select(NewsItem)
            .where(NewsItem.status == NewsStatus.NEW)
            .order_by(NewsItem.publish_time)
            .limit(batch_size)
        )
        items = rows.scalars().all()
        if not items:
            return 0
        await self.score_items(session, items)
        return len(items)

    async def score_items(self, session: AsyncSession, items: list[NewsItem]) -> dict[int, ScoreItemResult]:
        """评分入口：整批按小子批切片并发评分，按原始顺序合并结果后落库。"""
        if not items:
            return {}
        if not self.settings.has_llm_credentials():
            logger.warning("未配置任何模型 API Key，跳过评分（资讯保持 NEW 状态）")
            return {}

        expected_ids = {i.id for i in items}
        async with stage(
            logger,
            "评分 Agent",
            count=len(items),
            framework=self.settings.agent_framework,
            provider=self.settings.llm_default_provider,
            model=self.settings.model_for(self.settings.llm_default_provider, "scoring"),
            news_ids=[i.id for i in items][:20],
        ) as out:
            subs = self._split_subs(items)
            semaphore = get_semaphore("scoring", self.settings)

            async def _run_sub(sub: list[NewsItem]) -> ScoreBatchResult:
                # 调用方已持有信号量（scoring 并发上限 = 同时 in-flight 的子批数），
                # legacy 回退内部不再重复占用槽位
                async with semaphore:
                    return await self._score_sub_batch(sub)

            sub_results = await asyncio.gather(
                *(_run_sub(s) for s in subs), return_exceptions=True
            )
            result = self._merge_sub_results(items, subs, sub_results)
            out["path"] = (
                self.settings.agent_framework
                if len(subs) == 1
                else f"{self.settings.agent_framework}-subbatch"
            )
            out["sub_batches"] = len(subs)

            if not result.items:
                for item in items:
                    item.status = NewsStatus.SCORE_FAILED
                    item.retry_count = (item.retry_count or 0) + 1
                    item.last_error = "评分失败：模型未返回可解析结果"
                out.update(scored=0, missing=len(items), result="模型未返回可解析结果")
                return {}

            # 双跑灰度（默认关闭）：整批 legacy 再跑一遍，只记录差异不影响入库
            if self.settings.score_dual_run and self.settings.agent_framework == "langgraph":
                await self._compare_with_legacy(items, expected_ids, result)

            batch_id = uuid.uuid4().hex[:16]
            await self._persist(session, items, result, batch_id)

            # 补发下游事件：cli score 不经过事件总线，不补发的话这些资讯
            # 永远不会进入向量化与深度分析。publish 本身幂等（部分唯一索引），
            # 所以从 on_ingested 调用时重复发布是安全的。
            published = await self._publish_scored_events(session, result)

            out.update(
                batch_id=batch_id,
                scored=len(result.items),
                missing=len(items) - len(result.items),
                suspect=result.is_suspect,
                model=result.model,
                latency_ms=result.latency_ms,
                published=published,
            )
            return {r.id: r for r in result.items}

    async def _publish_scored_events(self, session: AsyncSession, result: ScoreBatchResult) -> int:
        bus = EventBus(session, worker_id="scoring")
        published = 0
        for item in result.items:
            event_id = await bus.publish(
                EventType.NEWS_SCORED,
                item.id,
                payload={"score": item.score, "band": band_for_score(item.score).value},
                priority=2,
            )
            if event_id is not None:
                published += 1
        if published:
            logger.info("已补发评分事件", count=published)
        return published

    # ------------------------------------------------------------------
    # 子批切分与执行
    # ------------------------------------------------------------------
    def _split_subs(self, items: list[NewsItem]) -> list[list[NewsItem]]:
        """按 scoring_sub_batch_size 连续切片；不足整批时保持单子批。"""
        size = max(1, int(self.settings.scoring_sub_batch_size))
        ordered = list(items)
        if len(ordered) <= size:
            return [ordered]
        return [ordered[i : i + size] for i in range(0, len(ordered), size)]

    async def _score_sub_batch(self, items: list[NewsItem]) -> ScoreBatchResult:
        """单个子批的完整评分流程：langgraph 优先，空/异常回退该子批 legacy。

        子批内编号重新从 1..k 开始，映射到真实 news_id 的解析逻辑天然支持，
        因此子批之间互不影响。调用方应已持有 scoring 信号量（并发上限）。
        """
        expected_ids = {i.id for i in items}
        if not items:
            return ScoreBatchResult()

        if self.settings.agent_framework == "langgraph":
            run: ScoringRun | None = None
            try:
                # 经 registry 拿缓存图：统一入口，框架细节对业务层透明
                graph = get_agent(AgentType.SCORING, self.settings)
                run = await run_scoring(items, self.settings, graph=graph)
            except Exception as exc:  # noqa: BLE001 - 图执行失败（含超时）回退 legacy
                logger.warning(
                    "LangGraph 评分失败，子批回退 legacy",
                    count=len(items),
                    error=str(exc)[:300],
                )
            if run is not None and run.items:
                logger.info(
                    "子批评分完成（langgraph）",
                    total=len(items),
                    scored=len(run.items),
                    model=run.model,
                    rounds=run.rounds,
                    latency_ms=run.latency_ms,
                    prompt_tokens=run.prompt_tokens,
                    completion_tokens=run.completion_tokens,
                )
                return self._run_to_batch_result(run)
            if run is not None:
                logger.warning(
                    "LangGraph 子批未产出任何评分，回退 legacy",
                    error=run.error,
                    rounds=run.rounds,
                )

        # legacy 回退：已处于 scoring 信号量保护内，locked=True 防止重复占用槽位
        return await self._score_legacy(items, expected_ids, locked=True)

    def _merge_sub_results(
        self,
        items: list[NewsItem],
        subs: list[list[NewsItem]],
        results: list[ScoreBatchResult | BaseException],
    ) -> ScoreBatchResult:
        """把各子批结果按原始顺序合并为整批结果。

        * items 顺序稳定（落库 / 补发事件按旧语义遍历）
        * latency_ms 取子批最大值（单批墙钟下界），token 累计
        * is_suspect 双重判定：任一子批 suspect 或整批同分集中（跨子批兜底）
        * 异常子批视为未评分，由 _persist 将对应资讯标 SCORE_FAILED
        """
        model = ""
        latency_ms = 0
        prompt_tokens = 0
        completion_tokens = 0
        suspect = False
        by_id: dict[int, ScoreItemResult] = {}

        for res in results:
            if res is None or isinstance(res, BaseException):
                logger.warning("评分子批异常，该子批按未评分处理", error=str(res)[:200] if res else "")
                continue
            for r in res.items:
                by_id.setdefault(r.id, r)
            if res.model and not model:
                model = res.model
            latency_ms = max(latency_ms, res.latency_ms)
            prompt_tokens += res.prompt_tokens
            completion_tokens += res.completion_tokens
            suspect = suspect or res.is_suspect

        merged = [by_id[n.id] for n in items if n.id in by_id]

        # 整批兜底：跨子批同分集中（如 3×10 全部同分）同样视为分布异常
        if len(merged) >= 5:
            top = max((r.score for r in merged), default=0)
            ratio = sum(1 for r in merged if r.score == top) / len(merged)
            suspect = suspect or ratio >= 0.8

        if subs and len(subs) > 1:
            logger.info(
                "评分子批合并",
                sub_batches=len(subs),
                scored=len(merged),
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        return ScoreBatchResult(
            items=merged,
            model=model,
            prompt_version=SCORING_VERSION,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            is_suspect=suspect,
        )

    # ------------------------------------------------------------------
    async def _score_legacy(
        self,
        items: list[NewsItem],
        expected_ids: set[int],
        *,
        locked: bool = False,
    ) -> ScoreBatchResult:
        """原实现：单次 JSON 调用 + 容错解析。

        locked=True 表示调用方已持有 scoring 信号量（子批并发场景），内部不再重复占用。
        """
        payload = self._build_payload(items)
        return await self._call_with_degradation(payload, expected_ids=expected_ids, locked=locked)

    def _run_to_batch_result(self, run: ScoringRun) -> ScoreBatchResult:
        """把图的执行结果转成与 legacy 一致的 ScoreBatchResult。"""
        items: list[ScoreItemResult] = []
        for news_id, scored in run.items.items():
            items.append(
                ScoreItemResult(
                    id=news_id,
                    score=scored.score,
                    reason=scored.reason,
                    tags=list(scored.tags),
                    entities=[
                        ScoreEntity(
                            type=EntityType(e.type) if e.type in EntityType._value2member_map_ else EntityType.MACRO,
                            code=e.code,
                            name=e.name,
                            confidence=e.confidence,
                        )
                        for e in scored.entities
                    ],
                    confidence=scored.confidence,
                )
            )
        return ScoreBatchResult(
            items=items,
            model=run.model,
            prompt_version=SCORING_VERSION,
            latency_ms=run.latency_ms,
            prompt_tokens=run.prompt_tokens,
            completion_tokens=run.completion_tokens,
            is_suspect=run.is_suspect,
        )

    async def _compare_with_legacy(
        self, items: list[NewsItem], expected_ids: set[int], result: ScoreBatchResult
    ) -> None:
        """双跑对比：整批再跑一遍 legacy 实现，只记录差异，不影响入库结果。"""
        try:
            legacy = await self._score_legacy(items, expected_ids)
        except Exception as exc:  # noqa: BLE001 - 对比失败不影响主流程
            logger.warning("双跑对比失败", error=str(exc)[:200])
            return

        primary_by_id = {r.id: r.score for r in result.items}
        legacy_by_id = {r.id: r.score for r in legacy.items}
        common = set(legacy_by_id) & set(primary_by_id)
        if not common:
            logger.warning(
                "评分双跑对比：两版结果无交集", legacy=len(legacy_by_id), langgraph=len(primary_by_id)
            )
            return

        exact = sum(1 for nid in common if legacy_by_id[nid] == primary_by_id[nid])
        band_same = sum(
            1
            for nid in common
            if band_for_score(legacy_by_id[nid]) == band_for_score(primary_by_id[nid])
        )
        diff = sum(abs(legacy_by_id[nid] - primary_by_id[nid]) for nid in common) / len(common)

        logger.info(
            "评分双跑对比",
            compared=len(common),
            exact_rate=round(exact / len(common), 3),
            band_same_rate=round(band_same / len(common), 3),
            mean_abs_diff=round(diff, 3),
            langgraph_model=result.model,
            langgraph_ms=result.latency_ms,
            legacy_ms=legacy.latency_ms,
        )

    # ------------------------------------------------------------------
    def _build_payload(self, items: list[NewsItem]) -> str:
        return build_payload(items, self.settings.scoring_max_content_chars)

    async def _call_with_degradation(
        self, payload: str, expected_ids: set[int], *, locked: bool = False
    ) -> ScoreBatchResult:
        """单次 JSON 调用 + 容错解析；失败返回空结果（由上层标记 SCORE_FAILED）。

        locked=True：调用方已持有 scoring 信号量（子批场景），直接调用不再占用槽位，
        避免同一协程嵌套获取信号量造成并发上限耗尽。
        """
        client = get_llm_client(self.settings)
        semaphore = get_semaphore("scoring", self.settings)

        async def _once() -> ScoreBatchResult:
            try:
                resp = await client.chat(
                    role="scoring",
                    system=SCORING_SYSTEM,
                    user=payload,
                    response_schema=SCORING_SCHEMA,
                    temperature=0,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("评分调用失败", error=str(exc)[:300])
                return ScoreBatchResult()
            return self._parse(
                resp.data, resp.model, resp.prompt_tokens, resp.completion_tokens,
                resp.latency_ms, expected_ids,
            )

        if locked:
            return await _once()
        async with semaphore:
            return await _once()

    @staticmethod
    def _parse(
        data,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        expected_ids: set[int],
    ) -> ScoreBatchResult:
        raw_items = (data or {}).get("items") if isinstance(data, dict) else None
        if not isinstance(raw_items, list):
            return ScoreBatchResult(model=model)

        id_list = sorted(expected_ids)
        # 模型按编号输出，编号 -> 真实 id 的映射
        index_to_id = {i: nid for i, nid in enumerate(id_list, start=1)}

        results: list[ScoreItemResult] = []
        seen: set[int] = set()
        for raw in raw_items:
            try:
                idx = int(raw.get("id"))
            except (TypeError, ValueError):
                continue
            news_id = index_to_id.get(idx)
            if news_id is None or news_id in seen:
                continue  # 丢弃幻觉 id 与重复项
            seen.add(news_id)
            raw_score = raw.get("score")
            if raw_score is None:
                continue
            try:
                score = clamp_score(raw_score)
            except (TypeError, ValueError):
                continue
            results.append(
                ScoreItemResult(
                    id=news_id,
                    score=score,
                    reason=str(raw.get("reason") or "")[:500],
                    tags=[str(t) for t in (raw.get("tags") or [])][:10],
                    entities=[
                        ScoreEntity(
                            type=e.get("type", "macro"),
                            code=e.get("code"),
                            name=e.get("name"),
                            confidence=float(e.get("confidence", 0.5) or 0.5),
                        )
                        for e in (raw.get("entities") or [])[:10]
                        if isinstance(e, dict)
                    ],
                    confidence=float(raw.get("confidence", 0.5) or 0.5),
                )
            )

        missing = expected_ids - seen
        if missing:
            logger.warning("批量评分存在漏评", missing=len(missing), total=len(expected_ids))

        # 分布异常检测：批内 80% 以上同分则标记 suspect
        is_suspect = False
        if len(results) >= 5:
            top = max((r.score for r in results), default=0)
            ratio = sum(1 for r in results if r.score == top) / len(results)
            is_suspect = ratio >= 0.8

        return ScoreBatchResult(
            items=results,
            model=model,
            prompt_version=SCORING_VERSION,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            is_suspect=is_suspect,
        )

    # ------------------------------------------------------------------
    async def _persist(
        self,
        session: AsyncSession,
        items: list[NewsItem],
        result: ScoreBatchResult,
        batch_id: str,
    ) -> None:
        by_id = {r.id: r for r in result.items}
        scored_at = now_utc()
        is_suspect = result.is_suspect

        for item in items:
            scored = by_id.get(item.id)
            if scored is None:
                item.status = NewsStatus.SCORE_FAILED
                item.retry_count = (item.retry_count or 0) + 1
                item.last_error = "批量评分未覆盖该条"
                continue

            band = band_for_score(scored.score)
            item.score = scored.score
            item.band = band
            item.score_reason = scored.reason
            item.score_model = result.model
            item.score_version = SCORING_VERSION
            item.scored_at = scored_at
            item.status = NewsStatus.SCORED
            item.tags = scored.tags
            item.retry_count = 0
            item.last_error = None

            session.add(
                NewsScore(
                    news_id=item.id,
                    score=scored.score,
                    band=band,
                    reason=scored.reason,
                    tags={"items": scored.tags},
                    confidence=scored.confidence,
                    is_suspect=is_suspect,
                    model=result.model,
                    prompt_version=SCORING_VERSION,
                    batch_id=batch_id,
                    latency_ms=result.latency_ms,
                )
            )

            for entity in scored.entities:
                try:
                    etype = EntityType(entity.type)
                except ValueError:
                    etype = EntityType.MACRO
                session.add(
                    NewsEntity(
                        news_id=item.id,
                        entity_type=etype,
                        code=entity.code,
                        name=entity.name,
                        confidence=entity.confidence,
                    )
                )

        await session.flush()
        logger.info(
            "评分完成",
            batch_id=batch_id,
            total=len(items),
            scored=len(result.items),
            suspect=is_suspect,
            model=result.model,
            latency_ms=result.latency_ms,
        )


async def score_news_by_id(session: AsyncSession, news_ids: list[int]) -> dict[int, ScoreItemResult]:
    """按 id 重算评分（运维入口）。"""
    rows = await session.execute(select(NewsItem).where(NewsItem.id.in_(news_ids)))
    items = rows.scalars().all()
    agent = ScoringAgent()
    return await agent.score_items(session, list(items))
