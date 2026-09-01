"""外部信息检索（宏观政策 Agent 补充信息的来源）。

未配置 WEB_SEARCH_* 时返回不可用状态，Agent 会据此降低置信度而不是报错。
"""
from __future__ import annotations

import httpx

from fin_news.core.config import Settings, get_settings
from fin_news.core.logging import get_logger

logger = get_logger("agents.tools.web_search")


class WebSearchUnavailable(RuntimeError):
    pass


async def web_search(query: str, max_results: int = 5, settings: Settings | None = None) -> list[dict]:
    settings = settings or get_settings()
    if not settings.web_search_enabled or not settings.web_search_base_url:
        raise WebSearchUnavailable("未配置外部搜索服务（WEB_SEARCH_ENABLED=false）")

    headers = {"Content-Type": "application/json"}
    if settings.web_search_api_key:
        headers["Authorization"] = f"Bearer {settings.web_search_api_key}"

    payload = {"query": query, "max_results": max_results}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(settings.web_search_base_url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # 兼容常见搜索 API 返回：results / data / items
    raw = data.get("results") or data.get("data") or data.get("items") or []
    out: list[dict] = []
    for item in raw[:max_results]:
        out.append(
            {
                "title": item.get("title") or item.get("name") or "",
                "url": item.get("url") or item.get("link") or "",
                "publisher": item.get("publisher") or item.get("source") or "",
                "published_at": item.get("published_at") or item.get("publishedDate") or "",
                "snippet": (item.get("snippet") or item.get("content") or "")[:500],
            }
        )
    return out


def format_results(results: list[dict]) -> str:
    if not results:
        return "（外部检索不可用或未配置）"
    return "\n".join(
        f"[{i}] {r.get('title')} ({r.get('publisher') or '来源未知'})\n    {r.get('snippet')}\n    {r.get('url')}"
        for i, r in enumerate(results, start=1)
    )
