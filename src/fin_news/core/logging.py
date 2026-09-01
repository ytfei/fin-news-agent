"""结构化日志（structlog），统一输出 JSON 行日志。

级别控制：
* `configure_logging(level=...)` 显式指定级别
* `configure_logging(verbosity=...)` 按 -v 数量映射：0=INFO、1=DEBUG、>=2=DEBUG（第三方库也放开）
"""
from __future__ import annotations

import logging
import sys
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
