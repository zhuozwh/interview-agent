"""验证 Tool 追踪的持久化、重启恢复和敏感正文隔离。"""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.storage import SQLiteDatabase, SQLiteToolTraceStore
from interview_agent.tools import ToolTraceRecord


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """追踪数据库位于自动清理的临时目录。"""
    with TemporaryDirectory(prefix="interview-agent-tool-trace-test-") as directory:
        yield Path(directory)


def _trace() -> ToolTraceRecord:
    """构造不含问题和笔记正文的完整追踪记录。"""
    return ToolTraceRecord(
        tool_call_id="call-1",
        trace_id="trace-1",
        tool_name="search_notes",
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:00.010000+00:00",
        duration_ms=10,
        status="success",
        result_count=1,
        parameters=(
            ("min_score", 0.45),
            ("query_length", 8),
            ("top_k", 5),
        ),
        result_ids=("chunk-1",),
    )


def test_records_and_recovers_trace_without_sensitive_columns(
    temporary_directory: Path,
) -> None:
    database = SQLiteDatabase(temporary_directory / "state.db")
    store = SQLiteToolTraceStore(database)
    store.initialize()
    store.record(_trace())

    restarted = SQLiteToolTraceStore(database)
    restarted.initialize()
    assert restarted.load_records("trace-1") == (_trace(),)
    assert restarted.load_records("other-trace") == ()

    with database.connection() as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tool_traces)")
        }
        serialized = connection.execute(
            "SELECT parameters_json, result_ids_json FROM tool_traces"
        ).fetchone()

    assert "query" not in columns
    assert "content" not in columns
    assert "relative_path" not in columns
    assert "query_length" in serialized[0]
    assert "chunk-1" in serialized[1]


def test_rejects_inconsistent_trace_and_detects_corrupt_json(
    temporary_directory: Path,
) -> None:
    database = SQLiteDatabase(temporary_directory / "state.db")
    store = SQLiteToolTraceStore(database)
    store.initialize()

    invalid = replace(_trace(), result_count=2)
    with pytest.raises(ValueError, match="result_count"):
        store.record(invalid)

    store.record(_trace())
    with database.connection() as connection:
        connection.execute(
            """
            UPDATE tool_traces
            SET parameters_json = ?
            WHERE tool_call_id = ?
            """,
            ("not-json", "call-1"),
        )

    with pytest.raises(sqlite3.DatabaseError, match="parameters JSON"):
        store.load_records()
