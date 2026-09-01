"""Tushare 客户端：限流、错误分类、重试（tushare SDK 是同步的，统一放线程池执行）。"""
from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd
import tushare as ts
from tenacity import RetryError, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from fin_news.core.config import Settings, get_settings
from fin_news.core.logging import get_logger

logger = get_logger("ingestion.tushare")

# 需要单独开通权限（与积分无关）的接口
PERMISSION_REQUIRED_APIS = {"news", "major_news"}

_RATE_LIMIT_HINTS = ("每分钟最多访问", "每分钟", "访问过于频繁", "too many requests", "rate limit")
_PERMISSION_HINTS = ("没有接口访问权限", "权限", "无此接口", "not authorized", "permission")


class TushareError(Exception):
    """Tushare 调用失败的基类。"""


class TushareRateLimitError(TushareError):
    """触发调用频率限制。"""


class TusharePermissionError(TushareError):
    """接口无权限（如 news / major_news 需单独开通资讯权限）。"""


class TushareConnectionError(TushareError):
    """网络 / 服务端异常。"""


def classify_error(api_name: str, exc: Exception) -> TushareError:
    msg = str(exc)
    lower = msg.lower()
    if any(h in lower for h in _RATE_LIMIT_HINTS):
        return TushareRateLimitError(msg)
    if api_name in PERMISSION_REQUIRED_APIS and any(h in lower for h in _PERMISSION_HINTS):
        return TusharePermissionError(msg)
    if any(h in msg for h in _PERMISSION_HINTS):
        return TusharePermissionError(msg)
    return TushareConnectionError(msg)


class TushareClient:
    """带 QPS 限制的 Tushare 查询封装。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.tushare_token:
            raise ValueError("未配置 TUSHARE_TOKEN，请在 .env 中填写")
        self._min_interval = 1.0 / max(0.1, self.settings.tushare_qps)
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._disabled_apis: set[str] = set()
        ts.set_token(self.settings.tushare_token)

    @property
    def disabled_apis(self) -> set[str]:
        return set(self._disabled_apis)

    def disable_api(self, api_name: str, reason: str) -> None:
        self._disabled_apis.add(api_name)
        logger.warning("接口已禁用", api=api_name, reason=reason)

    async def _acquire_slot(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = loop.time()

    def _call(self, api_name: str, **kwargs: Any) -> pd.DataFrame:
        pro = ts.pro_api(timeout=self.settings.tushare_timeout)
        return getattr(pro, api_name)(**kwargs)

    async def query(self, api_name: str, **kwargs: Any) -> pd.DataFrame:
        """执行一次查询，自动限流 + 重试（限流/网络类错误才重试，权限错误直接抛出）。"""
        if api_name in self._disabled_apis:
            raise TusharePermissionError(f"接口 {api_name} 已被禁用（无权限或连续失败）")

        async def _attempt() -> pd.DataFrame:
            await self._acquire_slot()
            try:
                return await asyncio.to_thread(self._call, api_name, **kwargs)
            except Exception as exc:  # noqa: BLE001 - tushare 异常类型不固定
                err = classify_error(api_name, exc)
                if isinstance(err, TusharePermissionError):
                    self.disable_api(api_name, str(err))
                raise err from exc

        try:
            return await retry(
                retry=retry_if_exception_type((TushareRateLimitError, TushareConnectionError)),
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=2, max=30),
                reraise=True,
            )(_attempt)()
        except RetryError as exc:  # pragma: no cover
            raise TushareError(str(exc)) from exc

    @staticmethod
    def to_records(df: pd.DataFrame | None) -> list[dict[str, Any]]:
        if df is None or len(df) == 0:
            return []
        return df.replace({float("nan"): None}).to_dict("records")


_client: TushareClient | None = None


def get_tushare_client(settings: Settings | None = None) -> TushareClient:
    global _client
    if _client is None:
        _client = TushareClient(settings)
    return _client
