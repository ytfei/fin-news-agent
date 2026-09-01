"""系统接口：健康检查。"""
from __future__ import annotations

from fastapi import APIRouter

from fin_news.api.deps import SessionDep
from fin_news.api.schemas import HealthOut
from fin_news.core.db import check_db
from fin_news.core.timeutil import now_utc
from fin_news.events.bus import EventBus

router = APIRouter(tags=["system"])

VERSION = "0.1.0"


@router.get("/health", response_model=HealthOut, summary="健康检查")
async def health(session: SessionDep):
    from fin_news.core.config import get_settings

    db_up = await check_db()
    backlog = 0
    if db_up:
        try:
            backlog = (await EventBus(session).backlog())["pending"]
        except Exception:  # noqa: BLE001
            backlog = -1

    llm_status = "up" if get_settings().has_llm_credentials() else "unknown"
    return HealthOut(
        status="ok" if db_up else "degraded",
        db="up" if db_up else "down",
        llm=llm_status,
        event_backlog=backlog,
        version=VERSION,
        time=now_utc(),
    )
