"""验证本地聊天正文的保存、清理和删除边界。"""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import pytest

from interview_agent.application.history_models import (
    HistoryCitation,
    HistoryTurn,
)
from interview_agent.storage import (
    SQLiteConversationHistoryStore,
    SQLiteDatabase,
)

_SESSION_1 = "11111111-1111-4111-8111-111111111111"
_SESSION_2 = "22222222-2222-4222-8222-222222222222"
_TRACE_1 = "33333333-3333-4333-8333-333333333333"
_TRACE_2 = "44444444-4444-4444-8444-444444444444"
_TRACE_3 = "55555555-5555-4555-8555-555555555555"


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """每项测试使用退出后自动清理的独立 SQLite。"""
    with TemporaryDirectory(prefix="interview-agent-history-test-") as directory:
        yield Path(directory)


def _create_store(
    root: Path,
    *,
    retention_days: int = 30,
    max_sessions: int = 100,
    max_turns: int = 100,
) -> tuple[SQLiteConversationHistoryStore, SQLiteDatabase]:
    """构造真实历史存储并完成幂等初始化。"""
    database = SQLiteDatabase(root / "history.sqlite3")
    store = SQLiteConversationHistoryStore(
        database,
        retention_days=retention_days,
        max_sessions=max_sessions,
        max_turns_per_session=max_turns,
    )
    store.initialize()
    store.initialize()
    return store, database


def _turn(
    trace_id: str,
    session_id: str,
    question: str,
    *,
    created_at: str | None = None,
) -> HistoryTurn:
    """生成只含展示字段的固定成功轮次。"""
    citation = HistoryCitation(
        citation_id="S1",
        source_namespace="notes",
        relative_path="cpp/raii.md",
        heading_path=("RAII",),
        start_line=3,
        end_line=6,
        score=0.91,
    )
    return HistoryTurn(
        trace_id=trace_id,
        session_id=session_id,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
        question=question,
        answer="RAII 把资源释放绑定到对象生命周期。[S1]",
        status="success",
        intent="knowledge_question",
        error_code=None,
        error_message=None,
        confidence="medium",
        citations=(citation,),
        follow_up_questions=("析构顺序如何确定？",),
    )


def test_history_round_trip_contains_only_ui_fields(
    temporary_directory: Path,
) -> None:
    """恢复问题、回答和相对引用，但协议没有证据正文或内部片段字段。"""
    store, database = _create_store(temporary_directory)
    store.record_turn(_turn(_TRACE_1, _SESSION_1, "RAII 是什么？"))

    summaries = store.list_sessions()
    assert len(summaries) == 1
    assert summaries[0].title == "RAII 是什么？"
    assert summaries[0].turn_count == 1
    session = store.load_session(_SESSION_1)
    assert session is not None
    assert session.turns[0].question == "RAII 是什么？"
    assert session.turns[0].citations[0].relative_path == "cpp/raii.md"

    with database.connection() as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(chat_turns)")
        }
    assert "evidence_text" not in columns
    assert "chunk_id" not in columns
    assert "fingerprint" not in columns
    assert "interview_record" not in columns


def test_history_limits_turns_and_sessions(
    temporary_directory: Path,
) -> None:
    """单会话和总会话上限都保留最近数据，不修改问题内容制造绿灯。"""
    store, _ = _create_store(
        temporary_directory,
        max_sessions=1,
        max_turns=1,
    )
    now = datetime.now(timezone.utc)
    store.record_turn(
        _turn(
            _TRACE_1,
            _SESSION_1,
            "第一问",
            created_at=(now - timedelta(minutes=2)).isoformat(),
        )
    )
    store.record_turn(
        _turn(
            _TRACE_2,
            _SESSION_1,
            "第二问",
            created_at=(now - timedelta(minutes=1)).isoformat(),
        )
    )
    first_session = store.load_session(_SESSION_1)
    assert first_session is not None
    assert [turn.question for turn in first_session.turns] == ["第二问"]

    store.record_turn(
        _turn(
            _TRACE_3,
            _SESSION_2,
            "另一个会话",
            created_at=now.isoformat(),
        )
    )
    assert store.load_session(_SESSION_1) is None
    assert [item.session_id for item in store.list_sessions()] == [_SESSION_2]


def test_history_prunes_expired_session(
    temporary_directory: Path,
) -> None:
    """超过保留期的会话会在下一次读取前删除。"""
    store, database = _create_store(temporary_directory, retention_days=1)
    store.record_turn(_turn(_TRACE_1, _SESSION_1, "即将过期"))
    expired = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with database.connection() as connection:
        connection.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?",
            (expired, _SESSION_1),
        )

    assert store.list_sessions() == ()
    with database.connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM chat_turns"
        ).fetchone()[0]
    assert count == 0


def test_delete_one_and_clear_do_not_delete_safe_agent_audit(
    temporary_directory: Path,
) -> None:
    """删除正文历史与 Phase 2 的最小审计表保持独立。"""
    store, database = _create_store(temporary_directory)
    store.record_turn(_turn(_TRACE_1, _SESSION_1, "会话一"))
    store.record_turn(_turn(_TRACE_2, _SESSION_2, "会话二"))
    with database.connection() as connection:
        connection.execute(
            "CREATE TABLE audit_sentinel (value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO audit_sentinel (value) VALUES ('safe-metadata')"
        )

    assert store.delete_session(_SESSION_1) is True
    assert store.delete_session(_SESSION_1) is False
    assert store.clear() == 1
    assert store.list_sessions() == ()
    with database.connection() as connection:
        value = connection.execute(
            "SELECT value FROM audit_sentinel"
        ).fetchone()[0]
    assert value == "safe-metadata"


def test_history_rejects_absolute_citation_and_duplicate_trace(
    temporary_directory: Path,
) -> None:
    """绝对路径不能落入历史，重复追踪也不能覆盖已有问答。"""
    store, _ = _create_store(temporary_directory)
    valid = _turn(_TRACE_1, _SESSION_1, "RAII 是什么？")
    invalid = HistoryTurn(
        trace_id=_TRACE_2,
        session_id=_SESSION_1,
        created_at=valid.created_at,
        question=valid.question,
        answer=valid.answer,
        status=valid.status,
        intent=valid.intent,
        error_code=None,
        error_message=None,
        confidence=valid.confidence,
        citations=(
            HistoryCitation(
                citation_id="S1",
                source_namespace="notes",
                relative_path="D:/private/raii.md",
                heading_path=("RAII",),
                start_line=1,
                end_line=2,
                score=0.9,
            ),
        ),
        follow_up_questions=(),
    )
    with pytest.raises(ValueError, match="must stay relative"):
        store.record_turn(invalid)

    store.record_turn(valid)
    with pytest.raises(sqlite3.IntegrityError):
        store.record_turn(valid)
