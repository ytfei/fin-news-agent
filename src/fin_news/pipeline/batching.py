"""攒批器：时间窗或条数窗先到先触发（用于批量评分，降低调用成本）。"""
from __future__ import annotations

import time
from typing import Generic, TypeVar

T = TypeVar("T")


class Batcher(Generic[T]):
    """按 (条数上限 / 等待窗口) 攒批。"""

    def __init__(self, batch_size: int, window_seconds: float) -> None:
        self.batch_size = max(1, batch_size)
        self.window_seconds = max(0.0, window_seconds)
        self._buffer: list[T] = []
        self._first_seen: float | None = None

    @property
    def size(self) -> int:
        return len(self._buffer)

    def add(self, items: list[T]) -> None:
        if not items:
            return
        if self._first_seen is None:
            self._first_seen = time.monotonic()
        self._buffer.extend(items)

    def ready(self) -> bool:
        if not self._buffer:
            return False
        if len(self._buffer) >= self.batch_size:
            return True
        if self._first_seen is None:
            return False
        return (time.monotonic() - self._first_seen) >= self.window_seconds

    def take(self) -> list[T]:
        batch = self._buffer[: self.batch_size]
        self._buffer = self._buffer[self.batch_size :]
        self._first_seen = time.monotonic() if self._buffer else None
        return batch

    def take_all(self) -> list[T]:
        batch = self._buffer
        self._buffer = []
        self._first_seen = None
        return batch
