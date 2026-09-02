"""外部信息检索（宏观政策 Agent 补充信息的来源）。

基于 Tavily Python SDK 的异步客户端实现，SDK 参考：
https://docs.tavily.com/sdk/python/reference

未配置 WEB_SEARCH_ENABLED / API Key 时抛 WebSearchUnavailable（能力缺失），
调用失败抛 WebSearchError（鉴权、配额、网络等），Agent 据此降低置信度而不是报错。
"""
from __future__ import annotations

import os
from typing import Any, Literal
from urllib.parse import urlparse

from tavily import AsyncTavilyClient
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    UsageLimitExceededError,
)
from tavily.errors import TimeoutError as TavilyTimeoutError

from fin_news.core.config import Settings, get_settings
from fin_news.core.logging import get_logger

logger = get_logger("agents.tools.web_search")

SearchTopic = Literal["general", "news", "finance"]
SearchDepth = Literal["basic", "advanced", "fast", "ultra-fast"]
SearchTimeRange = Literal["day", "week", "month", "year"]
AnswerMode = Literal["basic", "advanced"]

# Tavily 要求 max_results ∈ [0, 20]
MAX_RESULTS_CEILING = 20
SNIPPET_LIMIT = 500
TAVILY_API_KEY_ENV = "TAVILY_API_KEY"


class WebSearchUnavailable(RuntimeError):
    """外部检索未启用或未配置 Key。属于「能力缺失」，重试无意义。"""


class WebSearchError(RuntimeError):
    """外部检索调用失败（鉴权 / 配额 / 超时 / 网络）。"""


def _resolve_api_key(settings: Settings) -> str:
    """优先用配置里的 Key，其次回退到 SDK 约定的 TAVILY_API_KEY 环境变量。"""
    return settings.web_search_api_key or os.getenv(TAVILY_API_KEY_ENV) or ""


def _publisher(url: str) -> str:
    """Tavily 不返回来源名，用域名兜底（去掉 www. 前缀）。"""
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _api_base_url(raw: str) -> str | None:
    """把配置值归一成 SDK 需要的 base_url。

    SDK 会自行拼接 "/search"，而旧配置填的是完整端点（.../search），
    这里去掉尾巴避免请求打到 <base>/search/search。
    """
    value = (raw or "").strip().rstrip("/")
    if not value:
        return None
    return value[: -len("/search")] if value.endswith("/search") else value


async def web_search(
    query: str,
    max_results: int | None = None,
    settings: Settings | None = None,
    *,
    topic: SearchTopic | None = None,
    search_depth: SearchDepth | None = None,
    time_range: SearchTimeRange | None = None,
    days: int | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_answer: bool | AnswerMode = False,
    include_raw_content: bool | Literal["markdown", "text"] = False,
    client: AsyncTavilyClient | None = None,
) -> list[dict]:
    """用 Tavily 检索外部公开信息，返回归一化后的结果列表。

    每条结果：title / url / publisher / published_at / snippet / score。
    `max_results` 为空时取 settings.web_search_max_results；其余检索参数同理
    优先取显式入参、缺失时回落配置，方便按 Agent 场景微调。
    """
    settings = settings or get_settings()
    if not settings.web_search_enabled:
        raise WebSearchUnavailable("未启用外部检索（WEB_SEARCH_ENABLED=false）")

    api_key = _resolve_api_key(settings)
    if not api_key:
        raise WebSearchUnavailable(f"缺少 Tavily API Key（WEB_SEARCH_API_KEY 或 {TAVILY_API_KEY_ENV}）")

    # 显式传 0 也要算数（不能因为 falsy 就回落到配置默认值），再收敛到 Tavily 允许的范围
    requested = settings.web_search_max_results if max_results is None else max_results
    wanted = max(1, min(int(requested), MAX_RESULTS_CEILING))
    effective_topic = topic or settings.web_search_topic

    params: dict[str, Any] = {
        "query": query,
        "max_results": wanted,
        "topic": effective_topic,
        "search_depth": search_depth or settings.web_search_depth,
        # days 与 time_range 互斥，给 days 时让 time_range 失效
        "time_range": None if days else (time_range or settings.web_search_time_range or None),
        "days": days or None,
        "include_domains": (
            include_domains if include_domains is not None else list(settings.web_search_include_domains)
        ),
        "exclude_domains": (
            exclude_domains if exclude_domains is not None else list(settings.web_search_exclude_domains)
        ),
        "include_answer": include_answer,
        "include_raw_content": include_raw_content,
        "timeout": settings.web_search_timeout_seconds,
    }
    # 只做排序加权，不做严格过滤：宏观场景需要保留海外（英文）信源
    if settings.web_search_language:
        params["language"] = settings.web_search_language

    owns_client = client is None
    if client is None:
        client = AsyncTavilyClient(
            api_key=api_key,
            api_base_url=_api_base_url(settings.web_search_base_url),
        )
    try:
        response = await client.search(**params)
    except (MissingAPIKeyError, InvalidAPIKeyError) as exc:
        raise WebSearchError(f"Tavily API Key 无效：{exc}") from exc
    except UsageLimitExceededError as exc:
        raise WebSearchError(f"Tavily 配额不足或被限流：{exc}") from exc
    except (ForbiddenError, BadRequestError) as exc:
        raise WebSearchError(f"Tavily 请求被拒绝：{exc}") from exc
    except TavilyTimeoutError as exc:
        raise WebSearchError(f"Tavily 请求超时：{exc}") from exc
    except Exception as exc:  # noqa: BLE001 - 统一封装，避免 SDK 细节泄漏到 Agent
        raise WebSearchError(f"Tavily 调用失败：{type(exc).__name__}: {exc}") from exc
    finally:
        # 注入的 client 由调用方负责生命周期
        if owns_client:
            await client.close()

    out: list[dict] = []
    for item in (response.get("results") or [])[:wanted]:
        url = item.get("url") or ""
        out.append(
            {
                "title": item.get("title") or "",
                "url": url,
                "publisher": _publisher(url),
                # published_date 仅 topic=news 时返回
                "published_at": item.get("published_date") or "",
                "snippet": (item.get("content") or "")[:SNIPPET_LIMIT],
                "score": float(item.get("score") or 0.0),
            }
        )

    logger.info("外部检索完成", query=query, topic=effective_topic, hits=len(out))
    return out


def format_results(results: list[dict]) -> str:
    if not results:
        return "（外部检索不可用或未配置）"
    lines: list[str] = []
    for i, r in enumerate(results, start=1):
        meta = r.get("publisher") or "来源未知"
        if r.get("published_at"):
            meta = f"{meta} · {r['published_at']}"
        lines.append(f"[{i}] {r.get('title')} ({meta})\n    {r.get('snippet')}\n    {r.get('url')}")
    return "\n".join(lines)
