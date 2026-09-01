"""追问会话接口（支持 SSE 流式）。"""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from fin_news.agents.qa_agent import QAAgent, load_history
from fin_news.api.deps import DeviceIdDep, PaginationDep, SessionDep
from fin_news.api.errors import NotFoundError, ServiceUnavailableError
from fin_news.api.schemas import ChatMessageOut, ChatSessionOut
from fin_news.core.logging import get_logger
from fin_news.core.timeutil import now_utc
from fin_news.domain.schemas import _Base  # noqa: F401 - 保持依赖清晰
from fin_news.models.chat import ChatMessage, ChatSession

logger = get_logger("api.chat")

router = APIRouter(prefix="/chat", tags=["chat"])

DISCLAIMER = "AI 生成，仅供参考，不构成投资建议。"


class CreateSessionRequest(_Base):
    title: str | None = None
    context_filter: dict | None = None


class PostMessageRequest(_Base):
    content: str
    stream: bool = True
    context_filter: dict | None = None


@router.get("/sessions", summary="会话列表")
async def list_sessions(session: SessionDep, pagination: PaginationDep, device_id: DeviceIdDep):
    stmt = select(ChatSession).order_by(ChatSession.last_message_at.desc())
    if device_id:
        stmt = stmt.where(ChatSession.device_id == device_id)
    rows = (await session.execute(stmt.offset(pagination.offset).limit(pagination.page_size))).scalars().all()
    return {
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total": len(rows),
        "has_more": len(rows) == pagination.page_size,
        "items": [_session_out(r) for r in rows],
    }


@router.post("/sessions", response_model=ChatSessionOut, summary="创建会话", status_code=201)
async def create_session(session: SessionDep, device_id: DeviceIdDep, payload: CreateSessionRequest | None = None):
    obj = ChatSession(
        device_id=device_id,
        title=(payload.title if payload else None),
        context_filter=(payload.context_filter if payload else None) or {},
    )
    session.add(obj)
    await session.flush()
    return _session_out(obj)


@router.get("/sessions/{session_id}", response_model=ChatSessionOut, summary="会话详情")
async def get_session_detail(session_id: str, session: SessionDep):
    obj = await _get_session(session, session_id)
    return _session_out(obj)


def _session_out(obj: ChatSession) -> ChatSessionOut:
    """对外统一暴露 public_id（不暴露自增主键）。"""
    return ChatSessionOut(
        id=str(obj.public_id),
        title=obj.title,
        context_filter=obj.context_filter or {},
        message_count=obj.message_count,
        created_at=obj.created_at,
        last_message_at=obj.last_message_at,
    )


@router.delete("/sessions/{session_id}", status_code=204, summary="删除会话")
async def delete_session(session_id: str, session: SessionDep):
    obj = await _get_session(session, session_id)
    await session.delete(obj)


@router.get("/sessions/{session_id}/messages", summary="消息历史")
async def list_messages(session_id: str, session: SessionDep, pagination: PaginationDep):
    obj = await _get_session(session, session_id)
    rows = (
        await session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == obj.id)
            .order_by(ChatMessage.id.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).scalars().all()
    return {
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total": len(rows),
        "has_more": len(rows) == pagination.page_size,
        "items": [
            ChatMessageOut(
                id=str(m.id),
                session_id=str(obj.public_id),
                role=m.role,
                content=m.content,
                references=m.references or [],
                status=m.status,
                model=m.model,
                latency_ms=m.latency_ms,
                created_at=m.created_at,
                disclaimer=DISCLAIMER if m.role == "assistant" else None,
            )
            for m in reversed(rows)
        ],
    }


@router.post("/sessions/{session_id}/messages", summary="发起追问")
async def post_message(session_id: str, session: SessionDep, payload: PostMessageRequest):
    obj = await _get_session(session, session_id)
    agent = QAAgent()

    user_msg = ChatMessage(session_id=obj.id, role="user", content=payload.content, status="OK")
    session.add(user_msg)
    obj.message_count = (obj.message_count or 0) + 1
    obj.last_message_at = now_utc()
    await session.flush()

    history = await load_history(session, obj.id, limit=10)
    context_filter = payload.context_filter or obj.context_filter or {}

    if not payload.stream:
        try:
            answer = await agent.answer(session, payload.content, history, context_filter)
        except Exception as exc:  # noqa: BLE001
            raise ServiceUnavailableError(f"分析服务暂时不可用：{str(exc)[:200]}") from exc
        assistant = ChatMessage(
            session_id=obj.id,
            role="assistant",
            content=answer.content,
            references=answer.references,
            model=answer.model,
            latency_ms=answer.latency_ms,
            status=answer.status,
        )
        session.add(assistant)
        obj.message_count = (obj.message_count or 0) + 1
        await session.flush()
        return ChatMessageOut(
            id=str(assistant.id),
            session_id=str(obj.public_id),
            role="assistant",
            content=answer.content,
            references=answer.references,
            status=answer.status,
            model=answer.model,
            latency_ms=answer.latency_ms,
            created_at=assistant.created_at,
            disclaimer=DISCLAIMER,
        )

    return StreamingResponse(
        _event_stream(session, agent, obj, payload.content, history, context_filter),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ----------------------------------------------------------------------
async def _event_stream(session, agent: QAAgent, chat_session, question, history, context_filter):
    buffer: list[str] = []
    references: list[dict] = []
    meta: dict = {}
    status = "OK"

    try:
        async for event, data in agent.stream_answer(session, question, history, context_filter):
            if event == "delta":
                buffer.append(data)
                yield f"event: delta\ndata: {json.dumps({'text': data}, ensure_ascii=False)}\n\n"
            elif event == "references":
                references = data
            elif event == "done":
                meta = data
    except Exception as exc:  # noqa: BLE001
        status = "FAILED"
        logger.exception("追问失败", error=str(exc))
        yield f"event: error\ndata: {json.dumps({'detail': '分析服务暂时不可用，请稍后重试'}, ensure_ascii=False)}\n\n"
        return

    content = "".join(buffer).strip() or "当前资料不足以判断，请补充更具体的问题或稍后再试。"

    assistant = ChatMessage(
        session_id=chat_session.id,
        role="assistant",
        content=content,
        references=references,
        model=meta.get("model"),
        latency_ms=meta.get("latency_ms"),
        status=status,
    )
    session.add(assistant)
    chat_session.message_count = (chat_session.message_count or 0) + 1
    chat_session.last_message_at = now_utc()
    await session.flush()

    yield f"event: references\ndata: {json.dumps({'items': references}, ensure_ascii=False)}\n\n"
    yield (
        f"event: done\ndata: {json.dumps({'message_id': str(assistant.id), **meta}, ensure_ascii=False)}\n\n"
    )


async def _get_session(session, session_id: str) -> ChatSession:
    try:
        uid = UUID(session_id)
    except ValueError as exc:
        raise NotFoundError("会话不存在") from exc
    obj = (
        await session.execute(select(ChatSession).where(ChatSession.public_id == uid))
    ).scalar_one_or_none()
    if obj is None:
        raise NotFoundError("会话不存在")
    return obj
