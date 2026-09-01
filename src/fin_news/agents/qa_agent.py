"""追问 Agent：基于向量检索 + 行情数据回答「为什么 / 怎么样」。"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.agents.llm import get_llm_client, get_semaphore
from fin_news.agents.prompts import QA_SYSTEM, QA_USER_TEMPLATE, QA_VERSION
from fin_news.agents.tools.market_data import latest_trade_date, market_snapshot
from fin_news.agents.tools.retrieval import history_search
from fin_news.core.config import Settings, get_settings
from fin_news.core.logging import get_logger
from fin_news.core.timeutil import now
from fin_news.models.chat import ChatMessage
from fin_news.models.news import NewsItem

logger = get_logger("agents.qa")

REF_PATTERN = re.compile(r"\[ref:(\d+)\]")


@dataclass
class QAAnswer:
    content: str
    references: list[dict] = field(default_factory=list)
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    status: str = "OK"


class QAAgent:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------
    async def build_context(
        self, session: AsyncSession, question: str, context_filter: dict | None = None
    ) -> tuple[str, str, list[dict]]:
        filters = context_filter or {}
        start = (
            now() - timedelta(days=int(filters.get("recent_days") or self.settings.chat_recent_days))
        )
        top_k = int(filters.get("top_k") or self.settings.chat_top_k)

        try:
            hits = await history_search(
                session,
                question,
                top_k=top_k,
                start=start,
                min_score=filters.get("min_score"),
                codes=filters.get("codes"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("追问检索失败", error=str(exc)[:200])
            hits = []

        context_lines = []
        refs: list[dict] = []
        for hit in hits:
            time_str = hit.publish_time.strftime("%Y-%m-%d %H:%M") if hit.publish_time else "时间未知"
            context_lines.append(
                f"[ref:{hit.news_id}]（{time_str}，评分{hit.score}，{hit.title}）\n{(hit.snippet or '')[:400]}"
            )
            refs.append(
                {
                    "news_id": hit.news_id,
                    "title": hit.title,
                    "snippet": (hit.snippet or "")[:200],
                    "publish_time": hit.publish_time.isoformat() if hit.publish_time else None,
                    "score": hit.score,
                    "similarity": hit.similarity,
                }
            )

        market_text = "（行情数据暂不可用）"
        try:
            trade_date = await latest_trade_date(session)
            if trade_date:
                snapshot = await market_snapshot(session, trade_date)
                market_text = json.dumps(snapshot, ensure_ascii=False)[:1500]
        except Exception as exc:  # noqa: BLE001
            logger.warning("行情快照获取失败", error=str(exc)[:200])

        return "\n".join(context_lines) or "（未检索到相关资讯）", market_text, refs

    # ------------------------------------------------------------------
    async def answer(
        self,
        session: AsyncSession,
        question: str,
        history: list[ChatMessage] | None = None,
        context_filter: dict | None = None,
    ) -> QAAnswer:
        context, market, refs = await self.build_context(session, question, context_filter)
        prompt = QA_USER_TEMPLATE.format(
            question=_with_history(question, history),
            context=context,
            market=market,
        )
        client = get_llm_client(self.settings)
        semaphore = get_semaphore("qa", self.settings)

        started = time.perf_counter()
        async with semaphore:
            resp = await client.chat(
                role="qa",
                system=QA_SYSTEM,
                user=prompt,
                json_mode=False,
                temperature=0.3,
                run_id=None,
            )
        return QAAnswer(
            content=resp.content.strip(),
            references=self._match_refs(resp.content, refs),
            model=resp.model,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream_answer(
        self,
        session: AsyncSession,
        question: str,
        history: list[ChatMessage] | None = None,
        context_filter: dict | None = None,
    ):
        """流式回答：yield ("delta", text) / ("references", list) / ("done", dict)。"""
        context, market, refs = await self.build_context(session, question, context_filter)
        prompt = QA_USER_TEMPLATE.format(
            question=_with_history(question, history),
            context=context,
            market=market,
        )
        client = get_llm_client(self.settings)
        semaphore = get_semaphore("qa", self.settings)

        buffer: list[str] = []
        started = time.perf_counter()
        async with semaphore:
            async for delta in client.chat_stream(role="qa", system=QA_SYSTEM, user=prompt, temperature=0.3):
                buffer.append(delta)
                yield ("delta", delta)

        content = "".join(buffer).strip()
        yield ("references", self._match_refs(content, refs))
        yield (
            "done",
            {
                "model": self.settings.model_for(self.settings.llm_default_provider, "qa"),  # type: ignore[arg-type]
                "prompt_version": QA_VERSION,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "status": "OK",
            },
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _match_refs(content: str, refs: list[dict]) -> list[dict]:
        """只保留正文中实际引用过的来源，避免引用列表膨胀。"""
        cited = {int(m) for m in REF_PATTERN.findall(content or "")}
        if not cited:
            return refs[:3]
        matched = [r for r in refs if r["news_id"] in cited]
        return matched or refs[:3]


def _with_history(question: str, history: list[ChatMessage] | None) -> str:
    if not history:
        return question
    lines = []
    for msg in history[-6:]:
        role = "用户" if msg.role == "user" else "助手"
        lines.append(f"{role}：{msg.content[:300]}")
    return "历史对话：\n" + "\n".join(lines) + f"\n\n当前问题：{question}"


async def load_history(session: AsyncSession, session_id: int, limit: int = 10) -> list[ChatMessage]:
    rows = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    )
    return list(reversed(rows.scalars().all()))


async def resolve_news_titles(session: AsyncSession, news_ids: list[int]) -> dict[int, str]:
    rows = await session.execute(select(NewsItem.id, NewsItem.title).where(NewsItem.id.in_(news_ids)))
    return {r[0]: r[1] for r in rows.all()}
