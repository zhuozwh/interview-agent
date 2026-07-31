"""提供不包含业务路由逻辑的本地问答 HTTP 接口。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from interview_agent.agent import AgentRequest, AgentStatus
from interview_agent.application import ApplicationUnavailableError, AskService

router = APIRouter()


class AskApiRequest(BaseModel):
    """HTTP 输入只声明数据，不允许指定 Tool、namespace 或路径。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    question: str
    interview_record: str | None = None
    session_id: str | None = None


class CitationApiResponse(BaseModel):
    """交互层可展示的规范相对来源位置。"""

    citation_id: str
    source_namespace: str
    relative_path: str
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    score: float


class AgentErrorApiResponse(BaseModel):
    """不会回显正文或供应方异常的稳定错误。"""

    code: str
    message: str
    retryable: bool


class AskApiResponse(BaseModel):
    """一次问答、项目/简历解释或面试复盘的完整 HTTP 结果。"""

    session_id: str
    trace_id: str
    status: str
    intent: str | None
    route_reason: str
    answer: str | None
    citations: tuple[CitationApiResponse, ...]
    confidence: str | None
    follow_up_questions: tuple[str, ...]
    tool_call_ids: tuple[str, ...]
    llm_request_id: str | None
    error: AgentErrorApiResponse | None


class ServiceUnavailableResponse(BaseModel):
    """运行时尚未配置完成时的最小错误格式。"""

    detail: str


@router.post(
    "/ask",
    response_model=AskApiResponse,
    responses={503: {"model": ServiceUnavailableResponse}},
)
def post_ask(
    payload: AskApiRequest,
    request: Request,
) -> JSONResponse:
    """把 HTTP 数据交给应用用例，并映射稳定状态码。"""
    service: AskService = request.app.state.ask_service
    try:
        result = service.execute(
            AgentRequest(
                question=payload.question,
                interview_record=payload.interview_record,
            ),
            session_id=payload.session_id,
        )
    except ApplicationUnavailableError:
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Local data, index, or LLM configuration is not ready. "
                    "Fix the configuration and restart the service."
                )
            },
        )

    response = result.response
    body = AskApiResponse(
        session_id=result.session_id,
        trace_id=response.trace_id,
        status=response.status.value,
        intent=response.intent.value if response.intent is not None else None,
        route_reason=response.route_reason,
        answer=response.answer,
        citations=tuple(
            CitationApiResponse(
                citation_id=citation.citation_id,
                source_namespace=citation.source_namespace,
                relative_path=citation.relative_path,
                heading_path=citation.heading_path,
                start_line=citation.start_line,
                end_line=citation.end_line,
                score=citation.score,
            )
            for citation in response.citations
        ),
        confidence=(
            response.confidence.value
            if response.confidence is not None
            else None
        ),
        follow_up_questions=response.follow_up_questions,
        tool_call_ids=response.tool_call_ids,
        llm_request_id=response.llm_request_id,
        error=(
            AgentErrorApiResponse(
                code=response.error.code,
                message=response.error.message,
                retryable=response.error.retryable,
            )
            if response.error is not None
            else None
        ),
    )
    return JSONResponse(
        status_code=_http_status(response.status, response.error),
        content=body.model_dump(mode="json"),
    )


def _http_status(status: AgentStatus, error: object) -> int:
    """把领域状态稳定映射为本地 HTTP 状态，不检查错误正文。"""
    if status in {AgentStatus.SUCCESS, AgentStatus.NO_EVIDENCE}:
        return 200
    if status in {AgentStatus.INVALID_INPUT, AgentStatus.UNSUPPORTED}:
        return 422
    if status is AgentStatus.TOOL_ERROR or status is AgentStatus.LLM_ERROR:
        return 503 if getattr(error, "retryable", False) else 502
    if status is AgentStatus.INVALID_OUTPUT:
        return 502
    return 500


__all__ = [
    "AgentErrorApiResponse",
    "AskApiRequest",
    "AskApiResponse",
    "CitationApiResponse",
    "router",
]
