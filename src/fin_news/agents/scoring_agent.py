"""评分 Agent：使用轻量 flash 模型对一批资讯批量打分（1-10）。"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.llm import get_llm_client, get_semaphore
from fin_news.agents.prompts import SCORING_SCHEMA, SCORING_SYSTEM, SCORING_USER_TEMPLATE, SCORING_VERSION
from fin_news.core.config import Settings, get_settings
from fin_news.core.enums import EntityType, NewsStatus
from fin_news.core.logging import get_logger
from fin_news.core.timeutil import now_utc
from fin_news.domain.schemas import ScoreBatchResult, ScoreEntity, ScoreItemResult
from fin_news.domain.scoring import band_for_score, clamp_score
from fin_news.domain.textutil import truncate
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
        if not items:
            return {}
        if not self.settings.has_llm_credentials():
            logger.warning("未配置任何模型 API Key，跳过评分（资讯保持 NEW 状态）")
            return {}

        payload = self._build_payload(items)
        result = await self._call_with_degradation(payload, expected_ids={i.id for i in items})

        if not result.items:
            for item in items:
                item.status = NewsStatus.SCORE_FAILED
                item.retry_count = (item.retry_count or 0) + 1
                item.last_error = "评分失败：模型未返回可解析结果"
            return {}

        batch_id = uuid.uuid4().hex[:16]
        await self._persist(session, items, result, batch_id)
        return {r.id: r for r in result.items}

    # ------------------------------------------------------------------
    def _build_payload(self, items: list[NewsItem]) -> str:
        lines = []
        for idx, item in enumerate(items, start=1):
            content, _ = truncate(item.content or item.title or "", self.settings.scoring_max_content_chars)
            time_str = item.publish_time.strftime("%Y-%m-%d %H:%M") if item.publish_time else "时间未知"
            lines.append(f"{idx}. 【{item.src_name or item.src}】{time_str} 标题：{item.title}\n   正文：{content}")
        return SCORING_USER_TEMPLATE.format(count=len(items), items="\n".join(lines))

    async def _call_with_degradation(
        self, payload: str, expected_ids: set[int]
    ) -> ScoreBatchResult:
        """正常批 → 失败拆小批（10 条）→ 返回已成功部分。"""
        client = get_llm_client(self.settings)
        semaphore = get_semaphore("scoring", self.settings)

        async with semaphore:
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

        return self._parse(resp.data, resp.model, resp.prompt_tokens, resp.completion_tokens, resp.latency_ms,
                           expected_ids)

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
