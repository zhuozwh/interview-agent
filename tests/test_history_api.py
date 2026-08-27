"""验证聊天接口与历史读取、生命周期和删除的完整本地协议。"""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest

from interview_agent.agent import (
    AgentConfidence,
    AgentError,
    AgentIntent,
    AgentResponse,
    AgentStatus,
)
from interview_agent.application import (
    AskResult,
    LocalConversationHistoryService,
)
from interview_agent.core.config import Settings
from interview_agent.main import create_app
from interview_agent.rag import Citation
from interview_agent.storage import (
    SQLiteConversationHistoryStore,
    SQLiteDatabase,
)

_SESSION_ID = "11111111-1111-4111-8111-111111111111"
_TRACE_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """API 历史使用退出后自动清理的独立目录。"""
    with TemporaryDirectory(prefix="interview-agent-history-api-") as directory:
        yield Path(directory)


class FakeAskService:
    """返回带相对引用的确定性回答，不访问模型或文件。"""

    def execute(self, request, *, session_id=None):
        citation = Citation(
            citation_id="S1",
            chunk_id="internal-chunk-id",
            document_id="internal-document-id",
            source_type="markdown",
            source_namespace="notes",
            relative_path="cpp/raii.md",
            heading_path=("RAII",),
            start_line=3,
            end_line=6,
            fingerprint="a" * 64,
            score=0.9,
        )
        return AskResult(
            session_id=session_id or _SESSION_ID,
            response=AgentResponse(
                trace_id=_TRACE_ID,
                status=AgentStatus.SUCCESS,
                intent=AgentIntent.KNOWLEDGE_QUESTION,
                route_reason="knowledge_question_requires_notes",
                answer="RAII 把资源释放绑定到对象生命周期。[S1]",
                citations=(citation,),
                tool_call_ids=(),
                llm_request_id="provider-request",
                error=None,
                confidence=AgentConfidence.MEDIUM,
                follow_up_questions=("析构顺序如何确定？",),
            ),
        )


def _history_service(
    root: Path,
    *,
    enabled: bool = True,
) -> tuple[LocalConversationHistoryService, Settings]:
    """注入已初始化存储，使 API 测试不依赖真实数据源。"""
    database_path = root / "history.sqlite3"
    settings = Settings(
        database_path=database_path,
        session_history_enabled=enabled,
        _env_file=None,
    )
    store = SQLiteConversationHistoryStore(
        SQLiteDatabase(database_path),
        retention_days=30,
        max_sessions=100,
        max_turns_per_session=100,
    )
    if enabled:
        store.initialize()
    return LocalConversationHistoryService(settings, store=store), settings


async def _request(application, method: str, path: str, json=None):
    """通过内存 ASGI transport 调用接口，不监听端口。"""
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path, json=json)


def test_ask_persists_restores_and_deletes_controlled_history(
    temporary_directory: Path,
) -> None:
    """完整历史只保存当前问题、已校验回答和展示引用。"""
    history, settings = _history_service(temporary_directory)
    application = create_app(
        settings,
        ask_service=FakeAskService(),
        history_service=history,
    )
    response = asyncio.run(
        _request(
            application,
            "POST",
            "/ask",
            json={
                "question": "RAII 是什么？",
                "interview_record": "不应持久化的面试记录 candidate@example.com",
                "previous_question": "不应持久化的上一问",
                "session_id": _SESSION_ID,
            },
        )
    )
    assert response.status_code == 200
    assert response.json()["history_status"] == "saved"

    listing = asyncio.run(_request(application, "GET", "/api/history"))
    assert listing.status_code == 200
    listing_body = listing.json()
    assert listing_body["enabled"] is True
    assert listing_body["database_path"] == str(settings.database_path)
    assert listing_body["retention_days"] == 30
    assert listing_body["sessions"][0]["title"] == "RAII 是什么？"

    loaded = asyncio.run(
        _request(application, "GET", f"/api/history/{_SESSION_ID}")
    )
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["turns"][0]["question"] == "RAII 是什么？"
    assert body["turns"][0]["citations"][0]["relative_path"] == (
        "cpp/raii.md"
    )
    serialized = repr(body)
    assert "candidate@example.com" not in serialized
    assert "不应持久化的上一问" not in serialized
    assert "internal-chunk-id" not in serialized
    assert "internal-document-id" not in serialized
    assert "provider-request" not in serialized

    deleted = asyncio.run(
        _request(application, "DELETE", f"/api/history/{_SESSION_ID}")
    )
    assert deleted.status_code == 204
    missing = asyncio.run(
        _request(application, "GET", f"/api/history/{_SESSION_ID}")
    )
    assert missing.status_code == 404


def test_clear_history_returns_explicit_count(
    temporary_directory: Path,
) -> None:
    """全部清理有明确回执，重复操作稳定返回零。"""
    history, settings = _history_service(temporary_directory)
    application = create_app(
        settings,
        ask_service=FakeAskService(),
        history_service=history,
    )
    asyncio.run(
        _request(
            application,
            "POST",
            "/ask",
            json={"question": "RAII 是什么？", "session_id": _SESSION_ID},
        )
    )
    first = asyncio.run(_request(application, "DELETE", "/api/history"))
    second = asyncio.run(_request(application, "DELETE", "/api/history"))
    assert first.json() == {"deleted_sessions": 1}
    assert second.json() == {"deleted_sessions": 0}


def test_disabled_history_does_not_create_database(
    temporary_directory: Path,
) -> None:
    """明确关闭历史时，提问和列表都不会建立 SQLite 文件。"""
    history, settings = _history_service(temporary_directory, enabled=False)
    application = create_app(
        settings,
        ask_service=FakeAskService(),
        history_service=history,
    )
    response = asyncio.run(
        _request(application, "POST", "/ask", json={"question": "RAII？"})
    )
    listing = asyncio.run(_request(application, "GET", "/api/history"))
    assert response.json()["history_status"] == "disabled"
    assert listing.json()["enabled"] is False
    assert listing.json()["sessions"] == []
    assert not settings.database_path.exists()


def test_invalid_history_identity_and_storage_error_are_safe(
    temporary_directory: Path,
) -> None:
    """非法 ID 和内部路径异常都不会回显本机敏感信息。"""
    history, settings = _history_service(temporary_directory)
    application = create_app(
        settings,
        ask_service=FakeAskService(),
        history_service=history,
    )
    invalid = asyncio.run(
        _request(application, "GET", "/api/history/not-a-uuid")
    )
    assert invalid.status_code == 422

    class FailingHistory:
        info = history.info

        def list_sessions(self):
            from interview_agent.application import (
                ConversationHistoryUnavailableError,
            )

            raise ConversationHistoryUnavailableError(
                "D:/private/history.sqlite3 secret-value"
            )

    failed_app = create_app(
        settings,
        ask_service=FakeAskService(),
        history_service=FailingHistory(),
    )
    failed = asyncio.run(_request(failed_app, "GET", "/api/history"))
    assert failed.status_code == 503
    assert "D:/private" not in failed.text
    assert "secret-value" not in failed.text


def test_history_write_failure_keeps_answer_without_retry_signal(
    temporary_directory: Path,
) -> None:
    """历史写失败时回答仍可复制，并明确标记未保存。"""
    history, settings = _history_service(temporary_directory)

    class FailingRecordHistory:
        info = history.info

        def record(self, request, result):
            raise RuntimeError("D:/private/history.sqlite3")

    application = create_app(
        settings,
        ask_service=FakeAskService(),
        history_service=FailingRecordHistory(),
    )
    response = asyncio.run(
        _request(application, "POST", "/ask", json={"question": "RAII？"})
    )
    assert response.status_code == 200
    assert response.json()["answer"] is not None
    assert response.json()["history_status"] == "failed"
    assert "D:/private" not in response.text


def test_history_sanitizes_agent_error_before_persistence(
    temporary_directory: Path,
) -> None:
    """历史库不能保存供应方异常中的路径、密钥或任意错误码。"""
    history, settings = _history_service(temporary_directory)

    class MaliciousFailureService:
        def execute(self, request, *, session_id=None):
            return AskResult(
                session_id=session_id or _SESSION_ID,
                response=AgentResponse(
                    trace_id=_TRACE_ID,
                    status=AgentStatus.LLM_ERROR,
                    intent=None,
                    route_reason="answer_model_failed",
                    answer=None,
                    citations=(),
                    tool_call_ids=(),
                    llm_request_id=None,
                    error=AgentError(
                        code="bad-code:D:/private",
                        message="secret-key-value D:/private/resume.md",
                        retryable=False,
                    ),
                ),
            )

    application = create_app(
        settings,
        ask_service=MaliciousFailureService(),
        history_service=history,
    )
    created = asyncio.run(
        _request(
            application,
            "POST",
            "/ask",
            json={"question": "触发恶意错误", "session_id": _SESSION_ID},
        )
    )
    loaded = asyncio.run(
        _request(application, "GET", f"/api/history/{_SESSION_ID}")
    )

    assert created.status_code == 502
    turn = loaded.json()["turns"][0]
    assert turn["error_code"] == "agent_error"
    assert turn["error_message"] == (
        "The answer model could not complete the request."
    )
    assert "D:/private" not in loaded.text
    assert "secret-key-value" not in loaded.text
