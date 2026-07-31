"""组织一次提问的会话、Agent 调用和持久化追踪生命周期。"""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter_ns
from typing import Protocol
from uuid import UUID, uuid4

from interview_agent.agent import (
    AgentError,
    AgentRequest,
    AgentResponse,
    AgentStatus,
)
from interview_agent.application.models import (
    AgentTraceRecord,
    AgentTraceStore,
    AskResult,
)


class AgentExecutor(Protocol):
    """应用层只依赖 Agent 的一次同步执行能力。"""

    def execute(
        self,
        request: AgentRequest,
        *,
        trace_id: str | None = None,
    ) -> AgentResponse:
        """返回一次带统一追踪标识的执行结果。"""


class AskInterviewAgentUseCase:
    """生成会话和追踪身份，调用 Agent，并保存最小审计摘要。"""

    def __init__(
        self,
        *,
        agent: AgentExecutor,
        trace_store: AgentTraceStore,
    ) -> None:
        self.agent = agent
        self.trace_store = trace_store

    def execute(
        self,
        request: AgentRequest,
        *,
        session_id: str | None = None,
    ) -> AskResult:
        """执行一个单任务请求；追踪失败时不返回未审计的成功回答。"""
        trace_id = str(uuid4())
        normalized_session_id, session_error = _normalize_session_id(session_id)
        started_at = _utc_now()
        started_ns = perf_counter_ns()

        if session_error is not None:
            response = _application_error(
                trace_id=trace_id,
                status=AgentStatus.INVALID_INPUT,
                code="invalid_session_id",
                message="session_id must be a canonical UUID string.",
            )
        else:
            try:
                response = self.agent.execute(request, trace_id=trace_id)
            except Exception:
                response = _application_error(
                    trace_id=trace_id,
                    status=AgentStatus.INTERNAL_ERROR,
                    code="unexpected_agent_failure",
                    message="The Agent failed outside its stable protocol.",
                )
            if (
                not isinstance(response, AgentResponse)
                or response.trace_id != trace_id
            ):
                response = _application_error(
                    trace_id=trace_id,
                    status=AgentStatus.INTERNAL_ERROR,
                    code="invalid_agent_response",
                    message="The Agent returned an invalid response identity.",
                )

        completed_at = _utc_now()
        duration_ms = max(0, (perf_counter_ns() - started_ns) // 1_000_000)
        trace = _trace_from_response(
            request,
            response,
            session_id=normalized_session_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )
        try:
            self.trace_store.record(trace)
        except Exception:
            response = _application_error(
                trace_id=trace_id,
                status=AgentStatus.INTERNAL_ERROR,
                code="trace_write_failed",
                message="The Agent result could not be recorded.",
            )
        return AskResult(
            session_id=normalized_session_id,
            response=response,
        )


def _trace_from_response(
    request: object,
    response: AgentResponse,
    *,
    session_id: str,
    started_at: str,
    completed_at: str,
    duration_ms: int,
) -> AgentTraceRecord:
    """只提取安全元数据，绝不读取正文内容写入 SQLite。"""
    question = getattr(request, "question", None)
    interview_record = getattr(request, "interview_record", None)
    return AgentTraceRecord(
        trace_id=response.trace_id,
        session_id=session_id,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        status=response.status.value,
        intent=response.intent.value if response.intent is not None else None,
        route_reason=response.route_reason,
        tool_call_ids=response.tool_call_ids,
        llm_request_id=response.llm_request_id,
        citation_ids=tuple(
            citation.citation_id for citation in response.citations
        ),
        error_code=response.error.code if response.error is not None else None,
        question_length=len(question.strip()) if isinstance(question, str) else -1,
        interview_record_length=(
            len(interview_record.strip())
            if isinstance(interview_record, str)
            else 0
        ),
    )


def _normalize_session_id(value: object) -> tuple[str, AgentError | None]:
    """缺省时创建会话；外部 ID 只接受规范 UUID。"""
    generated = str(uuid4())
    if value is None:
        return generated, None
    if not isinstance(value, str):
        return generated, AgentError(
            code="invalid_session_id",
            message="session_id must be a canonical UUID string.",
            retryable=False,
        )
    try:
        normalized = str(UUID(value))
    except ValueError:
        return generated, AgentError(
            code="invalid_session_id",
            message="session_id must be a canonical UUID string.",
            retryable=False,
        )
    if normalized != value:
        return generated, AgentError(
            code="invalid_session_id",
            message="session_id must be a canonical UUID string.",
            retryable=False,
        )
    return normalized, None


def _application_error(
    *,
    trace_id: str,
    status: AgentStatus,
    code: str,
    message: str,
) -> AgentResponse:
    """构造不会携带正文或下游标识的应用边界错误。"""
    return AgentResponse(
        trace_id=trace_id,
        status=status,
        intent=None,
        route_reason="application_boundary_failed",
        answer=None,
        citations=(),
        tool_call_ids=(),
        llm_request_id=None,
        error=AgentError(
            code=code,
            message=message,
            retryable=False,
        ),
    )


def _utc_now() -> str:
    """返回带时区的 UTC 时间。"""
    return datetime.now(timezone.utc).isoformat()


__all__ = ["AgentExecutor", "AskInterviewAgentUseCase"]
