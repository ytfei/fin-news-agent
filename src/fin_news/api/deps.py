"""FastAPI 依赖：会话、分页、设备标识。"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from fin_news.core.db import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class Pagination:
    """分页参数。"""

    def __init__(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


PaginationDep = Annotated[Pagination, Depends()]


async def get_device_id(x_device_id: str | None = Header(default=None, alias="X-Device-Id")) -> str | None:
    """MVP 阶段使用匿名设备 ID；接入账号体系后替换为 Bearer JWT。"""
    return x_device_id


DeviceIdDep = Annotated[str | None, Depends(get_device_id)]
