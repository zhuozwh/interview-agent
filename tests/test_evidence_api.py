"""验证已保存引用到当前本地证据的受控 HTTP 协议。"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx

from interview_agent.application import LocalConversationHistoryService
from interview_agent.application.history_models import (
    HistoryCitation,
    HistorySession,
    HistorySessionSummary,
    HistoryTurn,
)
from interview_agent.core.config import Settings
from interview_agent.main import create_app
from interview_agent.storage import (
    SQLiteConversationHistoryStore,
    SQLiteDatabase,
)

_SESSION_ID = "11111111-1111-4111-8111-111111111111"
_TRACE_ID = "22222222-2222-4222-8222-222222222222"
_UNKNOWN_ID = "33333333-3333-4333-8333-333333333333"


async def _get(application, path: str) -> httpx.Response:
    """通过内存 ASGI 调用证据接口，不打开端口。"""
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.get(path)


def _settings(tmp_path: Path, *, history_enabled: bool = True) -> Settings:
    """建立三个相互隔离的合成资料域。"""
    knowledge = tmp_path / "knowledge"
    notes = knowledge / "interview"
    projects = knowledge / "projects"
    resume = knowledge / "resume"
    for directory in (notes, projects, resume):
        directory.mkdir(parents=True, exist_ok=True)
    return Settings(
        database_path=tmp_path / "runtime" / "history.sqlite3",
        session_history_enabled=history_enabled,
        markdown_source_directory=notes,
        project_source_directory=projects,
        resume_source_directory=resume,
        allowed_data_directories=(knowledge,),
        markdown_max_file_size_bytes=1024,
        _env_file=None,
    )


def _turn(
    *,
    namespace: str = "notes",
    relative_path: str = "cpp/raii.md",
) -> HistoryTurn:
    """生成一个只保存展示引用、不保存证据正文的合成轮次。"""
    return HistoryTurn(
        trace_id=_TRACE_ID,
        session_id=_SESSION_ID,
        created_at=datetime.now(timezone.utc).isoformat(),
        question="RAII 是什么？",
        answer="请核对引用。[S1]",
        status="success",
        intent="knowledge_question",
        error_code=None,
        error_message=None,
        confidence="medium",
        citations=(
            HistoryCitation(
                citation_id="S1",
                source_namespace=namespace,
                relative_path=relative_path,
                heading_path=("RAII", "生命周期"),
                start_line=3,
                end_line=4,
                score=0.9123,
            ),
        ),
        follow_up_questions=(),
    )


def _stored_application(
    tmp_path: Path,
    *,
    namespace: str = "notes",
    relative_path: str = "cpp/raii.md",
):
    """建立真实 SQLite 历史并返回使用默认证据服务的应用。"""
    settings = _settings(tmp_path)
    database = SQLiteDatabase(settings.database_path)
    store = SQLiteConversationHistoryStore(
        database,
        retention_days=30,
        max_sessions=100,
        max_turns_per_session=100,
    )
    store.initialize()
    store.record_turn(
        _turn(namespace=namespace, relative_path=relative_path)
    )
    history = LocalConversationHistoryService(settings, store=store)
    return create_app(settings, history_service=history), settings


def test_evidence_api_reads_current_excerpt_without_persisting_body(
    tmp_path: Path,
) -> None:
    """接口返回当前纯文本，但 SQLite 中仍只有引用元数据。"""
    application, settings = _stored_application(tmp_path)
    document = settings.markdown_source_directory / "cpp" / "raii.md"
    document.parent.mkdir()
    source_only = "<script>alert('SOURCE_ONLY_7f31')</script>"
    document.write_text(
        f"# RAII\n\n{source_only}\n资源随对象生命周期释放。\n尾行",
        encoding="utf-8",
    )

    response = asyncio.run(
        _get(
            application,
            f"/api/evidence/{_SESSION_ID}/{_TRACE_ID}/S1",
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == f"{source_only}\n资源随对象生命周期释放。"
    assert body["source_namespace"] == "notes"
    assert body["relative_path"] == "cpp/raii.md"
    assert body["heading_path"] == ["RAII", "生命周期"]
    assert body["citation_start_line"] == 3
    assert body["excerpt_end_line"] == 4
    assert body["score"] == 0.9123
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert str(tmp_path) not in response.text
    assert source_only.encode("utf-8") not in settings.database_path.read_bytes()


def test_evidence_api_rejects_invalid_and_forged_identities(
    tmp_path: Path,
) -> None:
    """路径、namespace 不属于客户端协议，伪造身份只能得到稳定错误。"""
    application, _ = _stored_application(tmp_path)
    cases = (
        (f"/api/evidence/not-a-uuid/{_TRACE_ID}/S1", 422),
        (f"/api/evidence/{_SESSION_ID}/not-a-uuid/S1", 422),
        (f"/api/evidence/{_SESSION_ID}/{_TRACE_ID}/s1", 422),
        (f"/api/evidence/{_UNKNOWN_ID}/{_TRACE_ID}/S1", 404),
        (f"/api/evidence/{_SESSION_ID}/{_UNKNOWN_ID}/S1", 404),
        (f"/api/evidence/{_SESSION_ID}/{_TRACE_ID}/S9", 404),
    )

    for path, expected_status in cases:
        response = asyncio.run(_get(application, path))
        assert response.status_code == expected_status
        assert response.headers["cache-control"] == "no-store"
        assert str(tmp_path) not in response.text


def test_evidence_api_reports_source_drift_without_leaking_paths(
    tmp_path: Path,
) -> None:
    """引用文件被移动后明确失效，不能回显本机路径或底层异常。"""
    application, settings = _stored_application(tmp_path)
    missing_path = settings.markdown_source_directory / "cpp" / "raii.md"

    response = asyncio.run(
        _get(
            application,
            f"/api/evidence/{_SESSION_ID}/{_TRACE_ID}/S1",
        )
    )

    assert not missing_path.exists()
    assert response.status_code == 410
    assert response.json() == {
        "detail": "The cited local source is no longer available."
    }
    assert str(tmp_path) not in response.text


def test_disabled_history_does_not_become_a_direct_file_reader(
    tmp_path: Path,
) -> None:
    """历史关闭时即使文件存在，也不能绕过保存身份直接展开。"""
    settings = _settings(tmp_path, history_enabled=False)
    document = settings.markdown_source_directory / "cpp" / "raii.md"
    document.parent.mkdir()
    document.write_text("# RAII\n\nsecret\nline", encoding="utf-8")
    history = LocalConversationHistoryService(settings)
    application = create_app(settings, history_service=history)

    response = asyncio.run(
        _get(
            application,
            f"/api/evidence/{_SESSION_ID}/{_TRACE_ID}/S1",
        )
    )

    assert response.status_code == 404
    assert not settings.database_path.exists()


def test_unknown_namespace_and_malicious_history_path_fail_closed(
    tmp_path: Path,
) -> None:
    """即使历史对象被伪造，未知域和目录穿越也不能读取任意文件。"""
    settings = _settings(tmp_path)
    summary = HistorySessionSummary(
        session_id=_SESSION_ID,
        title="伪造历史",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        turn_count=1,
    )

    class ForgedHistory:
        """绕过 SQLite 校验，模拟损坏或被篡改的历史适配器。"""

        def __init__(self, namespace: str, relative_path: str) -> None:
            self.turn = _turn(
                namespace=namespace,
                relative_path=relative_path,
            )

        def load_session(self, session_id: str):
            if session_id != _SESSION_ID:
                return None
            return HistorySession(summary=summary, turns=(self.turn,))

    for history in (
        ForgedHistory("private", "secret.md"),
        ForgedHistory("notes", "../private.md"),
    ):
        application = create_app(settings, history_service=history)
        response = asyncio.run(
            _get(
                application,
                f"/api/evidence/{_SESSION_ID}/{_TRACE_ID}/S1",
            )
        )
        assert response.status_code == 410
        assert str(tmp_path) not in response.text
