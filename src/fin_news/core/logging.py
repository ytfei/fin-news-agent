"""结构化日志（structlog），统一输出 JSON 行日志。

级别控制：
* `configure_logging(level=...)` 显式指定级别
* `configure_logging(verbosity=...)` 按 -v 数量映射：0=INFO、1=DEBUG、>=2=DEBUG（第三方库也放开）
"""
from __future__ import annotations

import logging
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog

from fin_news.core.config import get_settings

_CONFIGURED = False

# 默认抑制这些第三方库的日志，避免淹没业务日志（-vv 时放开到 DEBUG）
_NOISY_LOGGERS = ("apscheduler.executors.default", "apscheduler.scheduler", "httpx", "httpcore", "openai")


def configure_logging(level: str | None = None, verbosity: int = 0) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    settings = get_settings()

    if level is not None:
        log_level = level
    elif verbosity >= 1:
        log_level = "DEBUG"
    else:
        log_level = settings.log_level

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )
    # 第三方库默认 WARNING，-vv（verbosity>=2）时放开到 DEBUG 便于排查网络/接口细节
    third_party_level = logging.DEBUG if verbosity >= 2 else logging.WARNING
    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(third_party_level)
    _CONFIGURED = True


def get_logger(name: str = "fin_news", **bind: Any) -> structlog.stdlib.BoundLogger:
    configure_logging()
    return structlog.get_logger(name, **bind)


# ----------------------------------------------------------------------
# 执行路径追踪
# ----------------------------------------------------------------------
def bind_context(**fields: Any) -> None:
    """把字段绑定到当前上下文，之后该上下文内的所有日志都会自动带上。

    用于串联一次运行的完整路径（run_id / worker_id / event_type 等），
    无需在每层调用点手动透传。
    """
    structlog.contextvars.bind_contextvars(**fields)


def unbind_context(*keys: str) -> None:
    structlog.contextvars.unbind_contextvars(*keys)


def current_run_id() -> str | None:
    """取当前上下文绑定的 run_id（由 AgentRunTracker 绑定）。

    用途：模型调用分散在深层调用栈里（graph → ChatModel → callback），逐层透传
    run_id 会污染大量函数签名。改用 contextvars 后，落库侧（AuditCallbackHandler /
    LLMClient）直接读取即可把每次 LLM 调用归属到某次 Agent 运行。

    并发安全：structlog 的 contextvars 是 context-local 的，asyncio 下每个 task
    独立持有副本，并发的多个 Agent 运行不会互相串扰。
    """
    value = structlog.contextvars.get_contextvars().get("run_id")
    return str(value) if value else None


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


@asynccontextmanager
async def stage(
    logger: structlog.stdlib.BoundLogger,
    name: str,
    *,
    level: str = "info",
    **fields: Any,
) -> AsyncIterator[dict[str, Any]]:
    """记录一个阶段的「开始 / 结束 / 异常」，用于把执行路径串成一条链。

    开始 / 结束日志都按 level 输出（默认 info），结束日志自动带 elapsed_ms；
    阶段内可通过返回的字典补充结果字段：

        async with stage(logger, "评分 Agent", count=20) as out:
            result = await agent.score_items(session, items)
            out["scored"] = len(result)

    异常只记一行摘要（类型 + 消息），不重复打印堆栈：完整 traceback 由最外层
    处理者（handler / worker / cli）用 logger.exception 打一次即可，
    否则同一个异常会在路径的每一层都刷一份堆栈。
    """
    started = time.perf_counter()
    log = getattr(logger, level, logger.info)
    log(f"{name} 开始", **fields)
    out: dict[str, Any] = {}
    try:
        yield out
    except Exception as exc:
        logger.error(
            f"{name} 异常",
            **{
                **fields,
                **out,
                "elapsed_ms": elapsed_ms(started),
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            },
        )
        raise
    else:
        log(f"{name} 结束", **{**fields, **out, "elapsed_ms": elapsed_ms(started)})
