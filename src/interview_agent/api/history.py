"""提供本地聊天历史的读取和显式删除 HTTP 接口。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from interview_agent.application import (
    ConversationHistoryService,
    ConversationHistoryUnavailableError,
    HistorySession,
    HistorySessionSummary,
    HistoryTurn,
)

router = APIRouter(prefix="/api/history")


class HistoryCitationApiResponse(BaseModel):
    """会话恢复只返回界面可展示的相对引用。"""

    citation_id: str
    source_namespace: str
    relative_path: str
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    score: float


class HistoryTurnApiResponse(BaseModel):
    """一轮历史不包含证据正文、提示词或供应方错误。"""

    trace_id: str
    created_at: str
    question: str
    answer: str | None
    status: str
    intent: str | None
    error_code: str | None
    error_message: str | None
    confidence: str | None
    citations: tuple[HistoryCitationApiResponse, ...]
    follow_up_questions: tuple[str, ...]


class HistorySessionSummaryApiResponse(BaseModel):
    """侧边栏会话摘要。"""

    session_id: str
    title: str
    created_at: str
    updated_at: str
    turn_count: int


class HistoryListApiResponse(BaseModel):
    """同时解释当前正文生命周期和数据位置。"""

    enabled: bool
    database_path: str
    retention_days: int
    max_sessions: int
    max_turns_per_session: int
    sessions: tuple[HistorySessionSummaryApiResponse, ...]


class HistorySessionApiResponse(BaseModel):
    """一个可恢复会话。"""

    session: HistorySessionSummaryApiResponse
    turns: tuple[HistoryTurnApiResponse, ...]


class HistoryClearApiResponse(BaseModel):
    """全部清理操作的明确回执。"""

    deleted_sessions: int


@router.get("", response_model=HistoryListApiResponse)
def list_history(request: Request) -> JSONResponse:
    """返回最近会话和当前本地保留策略。"""
    service: ConversationHistoryService = request.app.state.history_service
    try:
        sessions = service.list_sessions()
    except ConversationHistoryUnavailableError:
        return _unavailable_response()
    info = service.info
    body = HistoryListApiResponse(
        enabled=info.enabled,
        database_path=info.database_path,
        retention_days=info.retention_days,
        max_sessions=info.max_sessions,
        max_turns_per_session=info.max_turns_per_session,
        sessions=tuple(_summary_response(item) for item in sessions),
    )
    return JSONResponse(content=body.model_dump(mode="json"))


@router.get("/{session_id}", response_model=HistorySessionApiResponse)
def get_history_session(session_id: str, request: Request) -> JSONResponse:
    """按规范 UUID 读取一个会话。"""
    service: ConversationHistoryService = request.app.state.history_service
    try:
        session = service.load_session(session_id)
    except ValueError:
        return JSONResponse(
            status_code=422,
            content={"detail": "session_id must be a canonical UUID string."},
        )
    except ConversationHistoryUnavailableError:
        return _unavailable_response()
    if session is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "Conversation history was not found."},
        )
    body = _session_response(session)
    return JSONResponse(content=body.model_dump(mode="json"))


@router.delete("/{session_id}", status_code=204)
def delete_history_session(session_id: str, request: Request):
    """显式删除一个会话的聊天正文和展示引用。"""
    service: ConversationHistoryService = request.app.state.history_service
    try:
        deleted = service.delete_session(session_id)
    except ValueError:
        return JSONResponse(
            status_code=422,
            content={"detail": "session_id must be a canonical UUID string."},
        )
    except ConversationHistoryUnavailableError:
        return _unavailable_response()
    if not deleted:
        return JSONResponse(
            status_code=404,
            content={"detail": "Conversation history was not found."},
        )
    return None


@router.delete("", response_model=HistoryClearApiResponse)
def clear_history(request: Request) -> JSONResponse:
    """清空全部聊天正文，保留不含正文的审计摘要。"""
    service: ConversationHistoryService = request.app.state.history_service
    try:
        deleted = service.clear()
    except ConversationHistoryUnavailableError:
        return _unavailable_response()
    body = HistoryClearApiResponse(deleted_sessions=deleted)
    return JSONResponse(content=body.model_dump(mode="json"))


def _summary_response(
    summary: HistorySessionSummary,
) -> HistorySessionSummaryApiResponse:
    """映射会话摘要。"""
    return HistorySessionSummaryApiResponse(
        session_id=summary.session_id,
        title=summary.title,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
        turn_count=summary.turn_count,
    )


def _turn_response(turn: HistoryTurn) -> HistoryTurnApiResponse:
    """映射一轮历史，不引入内部存储字段。"""
    return HistoryTurnApiResponse(
        trace_id=turn.trace_id,
        created_at=turn.created_at,
        question=turn.question,
        answer=turn.answer,
        status=turn.status,
        intent=turn.intent,
        error_code=turn.error_code,
        error_message=turn.error_message,
        confidence=turn.confidence,
        citations=tuple(
            HistoryCitationApiResponse(
                citation_id=citation.citation_id,
                source_namespace=citation.source_namespace,
                relative_path=citation.relative_path,
                heading_path=citation.heading_path,
                start_line=citation.start_line,
                end_line=citation.end_line,
                score=citation.score,
            )
            for citation in turn.citations
        ),
        follow_up_questions=turn.follow_up_questions,
    )


def _session_response(session: HistorySession) -> HistorySessionApiResponse:
    """映射完整会话。"""
    return HistorySessionApiResponse(
        session=_summary_response(session.summary),
        turns=tuple(_turn_response(turn) for turn in session.turns),
    )


def _unavailable_response() -> JSONResponse:
    """不把 SQLite、路径或配置异常正文返回给浏览器。"""
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "Local conversation history is not ready. "
                "Fix the configuration and restart the service."
            )
        },
    )


__all__ = ["router"]
