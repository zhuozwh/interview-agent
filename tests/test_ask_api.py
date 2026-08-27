"""验证 FastAPI /ask 只做协议转换和稳定错误映射。"""

import asyncio

import httpx

from interview_agent.agent import (
    AgentConfidence,
    AgentError,
    AgentIntent,
    AgentResponse,
    AgentStatus,
)
from interview_agent.application import (
    ApplicationUnavailableError,
    AskResult,
)
from interview_agent.core.config import Settings
from interview_agent.main import create_app
from interview_agent.rag import Citation

_SESSION_ID = "11111111-1111-4111-8111-111111111111"
_TRACE_ID = "22222222-2222-4222-8222-222222222222"
_TOOL_CALL_ID = "33333333-3333-4333-8333-333333333333"


class FakeAskService:
    """保存 HTTP 层传入的领域请求，并返回固定结果。"""

    def __init__(self, response: AgentResponse) -> None:
        self.response = response
        self.calls = []

    def execute(self, request, *, session_id=None):
        self.calls.append((request, session_id))
        return AskResult(
            session_id=session_id or _SESSION_ID,
            response=self.response,
        )


async def _post(application, json):
    """通过内存 ASGI transport 请求，不监听端口或访问网络。"""
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.post("/ask", json=json)


def _success_response() -> AgentResponse:
    """构造一条带安全相对引用的项目回答。"""
    citation = Citation(
        citation_id="S1",
        chunk_id="chunk-1",
        document_id="document-1",
        source_type="markdown",
        source_namespace="projects",
        relative_path="server.md",
        heading_path=("事件循环",),
        start_line=3,
        end_line=6,
        fingerprint="a" * 64,
        score=0.91,
    )
    return AgentResponse(
        trace_id=_TRACE_ID,
        status=AgentStatus.SUCCESS,
        intent=AgentIntent.PROJECT_CONTEXT,
        route_reason="project_question_requires_project_context",
        answer="项目当前使用单 Reactor。[S1]",
        citations=(citation,),
        tool_call_ids=(_TOOL_CALL_ID,),
        llm_request_id="provider-request-1",
        error=None,
        confidence=AgentConfidence.MEDIUM,
        follow_up_questions=("这个设计的取舍是什么？",),
    )


def test_ask_returns_answer_citations_and_trace_identity() -> None:
    """HTTP 层保留会话、引用和下游标识，不暴露绝对路径。"""
    service = FakeAskService(_success_response())
    application = create_app(
        Settings(_env_file=None),
        ask_service=service,
    )
    response = asyncio.run(
        _post(
            application,
            {
                "question": "我的项目当前实现状态是什么？",
                "session_id": _SESSION_ID,
            },
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == _SESSION_ID
    assert body["trace_id"] == _TRACE_ID
    assert body["status"] == "success"
    assert body["intent"] == "project_context"
    assert body["confidence"] == "medium"
    assert body["citations"] == [
        {
            "citation_id": "S1",
            "source_namespace": "projects",
            "relative_path": "server.md",
            "heading_path": ["事件循环"],
            "start_line": 3,
            "end_line": 6,
            "score": 0.91,
        }
    ]
    assert service.calls[0][0].question == "我的项目当前实现状态是什么？"
    assert service.calls[0][1] == _SESSION_ID


def test_ask_passes_limited_previous_question_context() -> None:
    """HTTP 层只透传上一轮问题，不接受上一轮回答或证据。"""
    service = FakeAskService(_success_response())
    application = create_app(
        Settings(_env_file=None),
        ask_service=service,
    )
    response = asyncio.run(
        _post(
            application,
            {
                "question": "它如何处理连接？",
                "previous_question": "我的项目中 Reactor 当前如何实现？",
                "session_id": _SESSION_ID,
            },
        )
    )

    assert response.status_code == 200
    request, session_id = service.calls[0]
    assert request.question == "它如何处理连接？"
    assert request.previous_question == "我的项目中 Reactor 当前如何实现？"
    assert session_id == _SESSION_ID


def test_ask_passes_interview_record_without_allowing_tool_override() -> None:
    """复盘记录进入应用层；调用方不能额外指定 Tool 或 namespace。"""
    service = FakeAskService(
        AgentResponse(
            trace_id=_TRACE_ID,
            status=AgentStatus.SUCCESS,
            intent=AgentIntent.INTERVIEW_REVIEW,
            route_reason="provided_interview_record_requires_review",
            answer="复盘完成。",
            citations=(),
            tool_call_ids=(),
            llm_request_id="provider-request-1",
            error=None,
            confidence=AgentConfidence.NOT_APPLICABLE,
        )
    )
    application = create_app(
        Settings(_env_file=None),
        ask_service=service,
    )
    response = asyncio.run(
        _post(
            application,
            {
                "question": "请复盘",
                "interview_record": "面试官问了 RAII。",
            },
        )
    )
    assert response.status_code == 200
    assert service.calls[0][0].interview_record == "面试官问了 RAII。"

    rejected = asyncio.run(
        _post(
            application,
            {
                "question": "读取任意数据",
                "tool_name": "read_file",
                "source_namespace": "../private",
            },
        )
    )
    assert rejected.status_code == 422
    assert len(service.calls) == 1


def test_ask_maps_stable_agent_failures_to_http_status() -> None:
    """输入、下游临时失败和非法模型输出使用不同 HTTP 类别。"""
    cases = (
        (AgentStatus.INVALID_INPUT, False, 422),
        (AgentStatus.TOOL_ERROR, True, 503),
        (AgentStatus.LLM_ERROR, False, 502),
        (AgentStatus.INVALID_OUTPUT, False, 502),
        (AgentStatus.INTERNAL_ERROR, False, 500),
    )
    for status, retryable, expected_status in cases:
        service = FakeAskService(
            AgentResponse(
                trace_id=_TRACE_ID,
                status=status,
                intent=None,
                route_reason="test_failure",
                answer=None,
                citations=(),
                tool_call_ids=(),
                llm_request_id=None,
                error=AgentError(
                    code="test_error",
                    message="A safe test error.",
                    retryable=retryable,
                ),
            )
        )
        application = create_app(
            Settings(_env_file=None),
            ask_service=service,
        )
        response = asyncio.run(
            _post(application, {"question": "测试问题"})
        )
        assert response.status_code == expected_status
        assert response.json()["error"]["code"] == "test_error"


def test_default_ask_service_is_lazy_and_reports_missing_configuration() -> None:
    """没有 LLM 密钥时健康检查可用，/ask 明确返回服务未就绪。"""
    application = create_app(Settings(_env_file=None))
    response = asyncio.run(
        _post(application, {"question": "智能指针是什么？"})
    )
    assert response.status_code == 503
    assert "configuration is not ready" in response.json()["detail"]


def test_api_does_not_expose_runtime_exception_details() -> None:
    """运行时异常可能含路径或密钥，但 HTTP 只返回固定安全文案。"""

    class UnavailableService:
        def execute(self, request, *, session_id=None):
            raise ApplicationUnavailableError(
                "D:/private/resume.md secret-key-value"
            )

    application = create_app(
        Settings(_env_file=None),
        ask_service=UnavailableService(),
    )
    response = asyncio.run(
        _post(application, {"question": "测试问题"})
    )
    body = response.text
    assert response.status_code == 503
    assert "D:/private" not in body
    assert "secret-key-value" not in body


def test_api_sanitizes_agent_error_code_and_message() -> None:
    """恶意错误码和供应方正文不能穿透到本地 HTTP 客户端。"""
    service = FakeAskService(
        AgentResponse(
            trace_id=_TRACE_ID,
            status=AgentStatus.LLM_ERROR,
            intent=None,
            route_reason="answer_model_failed",
            answer=None,
            citations=(),
            tool_call_ids=(),
            llm_request_id=None,
            error=AgentError(
                code="LLM_ERROR\nD:/private",
                message="D:/private/resume.md secret-key-value",
                retryable=True,
            ),
        )
    )
    application = create_app(
        Settings(_env_file=None),
        ask_service=service,
    )
    response = asyncio.run(
        _post(application, {"question": "触发恶意错误"})
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error"] == {
        "code": "agent_error",
        "message": "The answer model could not complete the request.",
        "retryable": True,
    }
    assert "D:/private" not in response.text
    assert "secret-key-value" not in response.text


def test_api_rejects_non_string_error_code_and_truthy_retry_flag() -> None:
    """宽松 dataclass 被误用时，类型异常也不能扩大公开错误行为。"""
    service = FakeAskService(
        AgentResponse(
            trace_id=_TRACE_ID,
            status=AgentStatus.LLM_ERROR,
            intent=None,
            route_reason="answer_model_failed",
            answer=None,
            citations=(),
            tool_call_ids=(),
            llm_request_id=None,
            error=AgentError(
                code=123,  # type: ignore[arg-type]
                message="secret-key-value",
                retryable="yes",  # type: ignore[arg-type]
            ),
        )
    )
    application = create_app(
        Settings(_env_file=None),
        ask_service=service,
    )
    response = asyncio.run(
        _post(application, {"question": "触发类型异常"})
    )

    assert response.status_code == 502
    assert response.json()["error"] == {
        "code": "agent_error",
        "message": "The answer model could not complete the request.",
        "retryable": False,
    }
    assert "secret-key-value" not in response.text
