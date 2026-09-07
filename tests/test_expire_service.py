"""时效策略服务（services/expire_service.py）的软去重与批量清理逻辑测试。

仓库没有数据库 fixture，采用注入式假实现：假 session / 假 EventBus，验证
「手动触发走提优先级插队而非静默失效」「批量清理只扫 PENDING 且跳过手动事件」
这两个最容易出错的语义。
"""
from __future__ import annotations

from sqlalchemy.sql.elements import TextClause

from fin_news.core.enums import EventStatus, NewsStatus
from fin_news.services import expire_service
from fin_news.services.expire_service import expire_stale_events, request_analysis


# ----------------------------------------------------------------------
# 假对象
# ----------------------------------------------------------------------
class _NewsObj:
    def __init__(self, news_id: int, status=NewsStatus.EMBEDDED):
        self.id = news_id
        self.status = status


class _FirstResult:
    """支持 .first() 的结果（request_analysis 的 select 查询）。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _ReqSession:
    """request_analysis 专用假 session：区分 get / select / update。"""

    def __init__(self, news: _NewsObj | None, existing: tuple | None):
        self.news = news
        self.existing = existing
        self.updates: list = []
        self.got_id: int | None = None

    async def get(self, model, news_id):
        self.got_id = news_id
        return self.news

    async def execute(self, stmt, *args, **kwargs):
        from sqlalchemy.sql.dml import Update

        if isinstance(stmt, Update):
            self.updates.append(stmt)
            return _FirstResult([])
        return _FirstResult([self.existing] if self.existing else [])


class _FakeBus:
    """假 EventBus：记录 publish 调用参数。"""

    def __init__(self, session, worker_id="w"):
        self.published: list[dict] = []

    async def publish(self, event_type, aggregate_id, payload=None, priority=None, **kw):
        self.published.append(
            {
                "event_type": event_type,
                "aggregate_id": aggregate_id,
                "payload": payload,
                "priority": priority,
            }
        )
        return 1


# ----------------------------------------------------------------------
# request_analysis：软去重分支
# ----------------------------------------------------------------------
async def test_publish_when_no_pending_event(monkeypatch):
    """无待处理事件 → 发布高优先级事件（不静默失效）。"""
    bus = _FakeBus(None)
    monkeypatch.setattr(expire_service, "EventBus", lambda session, *a, **kw: bus)
    settings = _settings(manual_priority=10)

    result = await request_analysis(
        _ReqSession(_NewsObj(1), None), 1, force=False, settings=settings
    )

    assert result is not None and result["queued"] is True
    assert bus.published, "无待处理事件时必须发布新事件"
    assert bus.published[0]["priority"] == 10
    assert bus.published[0]["payload"] == {"manual": True, "force": False}


async def test_escalate_priority_when_pending(monkeypatch):
    """已有 PENDING 事件 → 提升优先级插队，而非再次 publish（否则会被软去重吞掉）。"""
    bus = _FakeBus(None)
    monkeypatch.setattr(expire_service, "EventBus", lambda session, *a, **kw: bus)
    settings = _settings(manual_priority=10)
    session = _ReqSession(_NewsObj(1), (99, EventStatus.PENDING))

    result = await request_analysis(session, 1, force=True, settings=settings)

    assert result is not None and result["escalated"] is True
    assert not bus.published, "已有 PENDING 事件时不应重复 publish"
    assert session.updates, "应通过 UPDATE 提升优先级"
    assert result["priority"] == 10


async def test_returns_in_progress_when_processing(monkeypatch):
    """已有 PROCESSING 事件 → 直接告知「处理中」，不重复触发。"""
    bus = _FakeBus(None)
    monkeypatch.setattr(expire_service, "EventBus", lambda session, *a, **kw: bus)
    session = _ReqSession(_NewsObj(1), (99, EventStatus.PROCESSING))

    result = await request_analysis(session, 1)

    assert result is not None and result["in_progress"] is True
    assert result["queued"] is False
    assert not bus.published and not session.updates


async def test_backfills_expired_to_embedded(monkeypatch):
    """EXPIRED 资讯手动触发时回填为 EMBEDDED，使其重新满足 handler 查询条件。"""
    bus = _FakeBus(None)
    monkeypatch.setattr(expire_service, "EventBus", lambda session, *a, **kw: bus)
    news = _NewsObj(1, status=NewsStatus.EXPIRED)
    session = _ReqSession(news, None)

    await request_analysis(session, 1)

    assert news.status == NewsStatus.EMBEDDED, "EXPIRED 应回填为 EMBEDDED"


async def test_returns_none_when_news_missing(monkeypatch):
    """资讯不存在 → 返回 None，由调用方抛 404。"""
    bus = _FakeBus(None)
    monkeypatch.setattr(expire_service, "EventBus", lambda session, *a, **kw: bus)
    result = await request_analysis(_ReqSession(None, None), 999)
    assert result is None


# ----------------------------------------------------------------------
# expire_stale_events：批量清理
# ----------------------------------------------------------------------
class _ScalarsResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _RowcountResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _ExpireSession:
    """expire_stale_events 专用假 session：区分 text 查询与 update。"""

    def __init__(self, acked_ids):
        self._acked = list(acked_ids)
        self.executed_text: str | None = None
        self.update_calls = 0

    async def execute(self, stmt, params=None, *args, **kwargs):
        if isinstance(stmt, TextClause):
            self.executed_text = str(stmt)
            return _ScalarsResult(self._acked)
        self.update_calls += 1
        return _RowcountResult(len(self._acked))


def _settings(**kw):
    from fin_news.core.config import Settings

    base = dict(manual_priority=10, analysis_max_age_hours=24)
    base.update(kw)
    return Settings(_env_file=None, **base)


async def test_expire_stale_events_stock_band_when_zero_hours():
    """max_age_hours=0 时仍清理 STOCK 档（score 4-5），但不再有时效条件。"""
    session = _ExpireSession([1, 2, 3])
    result = await expire_stale_events(session, max_age_hours=0)
    assert result == {"events_acked": 3, "news_expired": 3}
    sql = session.executed_text or ""
    assert "n.score > 3" in sql and "n.score <= 5" in sql, "应清理 STOCK 档"
    assert "publish_time <" not in sql, "max_age_hours=0 不应有时效条件"
    assert session.update_calls == 1


async def test_expire_stale_events_bulk_sql_shape():
    """批量清理 SQL 必须：只扫 PENDING、跳过手动事件、按 publish_time 判过期。"""
    session = _ExpireSession([1, 2, 3])
    result = await expire_stale_events(session, max_age_hours=24)

    assert result == {"events_acked": 3, "news_expired": 3}
    sql = session.executed_text or ""
    assert "status = 'PENDING'" in sql, "只扫 PENDING，不碰 PROCESSING"
    assert "payload->>'manual'" in sql, "跳过手动触发的事件"
    assert "publish_time <" in sql, "按 publish_time 判定过期"
    assert "n.score > 3" in sql and "n.score <= 5" in sql, "应清理 STOCK 档"
    assert "news.embedded" in sql, "只清理分析事件"
    assert session.update_calls == 1, "news_item 的 EXPIRED 更新应执行一次"
