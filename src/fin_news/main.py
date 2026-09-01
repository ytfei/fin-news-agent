"""服务启动入口。

    uv run python -m fin_news.main
"""
from __future__ import annotations

import uvicorn

from fin_news.api.app import create_app
from fin_news.core.config import get_settings
from fin_news.core.logging import configure_logging, get_logger

logger = get_logger("main")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = create_app(settings)
    logger.info(
        "服务启动",
        host="0.0.0.0",
        port=8000,
        env=settings.env,
        sources=settings.news_sources,
        llm_ready=settings.has_llm_credentials(),
    )
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
