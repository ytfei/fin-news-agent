"""并发闸门：按角色限制并发，避免打满模型侧 QPS 或本地连接池。"""
from __future__ import annotations

import asyncio

from fin_news.core.config import Settings, get_settings

_semaphores: dict[str, asyncio.Semaphore] = {}


def get_semaphore(role: str, settings: Settings | None = None) -> asyncio.Semaphore:
    settings = settings or get_settings()
    if role not in _semaphores:
        limits = {
            "scoring": settings.scoring_concurrency,
            "analysis": settings.analysis_concurrency,
            "qa": settings.analysis_concurrency,
            "embedding": settings.scoring_concurrency,
        }
        _semaphores[role] = asyncio.Semaphore(max(1, limits.get(role, 2)))
    return _semaphores[role]


class BudgetGuard:
    """日预算软限制（进程内计数，够用即可；跨进程可改为 Redis）。"""

    def __init__(self, daily_cent_limit: float = 0) -> None:
        self.limit = daily_cent_limit
        self._used = 0.0

    @property
    def used(self) -> float:
        return self._used

    @property
    def exceeded(self) -> bool:
        return self.limit > 0 and self._used >= self.limit

    def add(self, cent: float) -> None:
        self._used += cent


budget_guard = BudgetGuard()
