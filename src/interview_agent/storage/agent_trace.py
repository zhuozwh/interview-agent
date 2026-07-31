"""使用 SQLite 保存不含正文的 Agent 会话与调用追踪。"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING
from uuid import UUID

from interview_agent.storage.sqlite import SQLiteDatabase

if TYPE_CHECKING:
    from interview_agent.application.models import AgentTraceRecord


class SQLiteAgentTraceStore:
    """集中管理单用户会话和 Agent 请求追踪。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def initialize(self) -> None:
        """幂等创建会话、追踪表和查询索引。"""
        with self.database.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    last_trace_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_traces (
                    trace_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                    status TEXT NOT NULL,
                    intent TEXT,
                    route_reason TEXT NOT NULL,
                    tool_call_ids_json TEXT NOT NULL,
                    llm_request_id TEXT,
                    citation_ids_json TEXT NOT NULL,
                    error_code TEXT,
                    question_length INTEGER NOT NULL CHECK(question_length >= -1),
                    interview_record_length INTEGER NOT NULL
                        CHECK(interview_record_length >= 0),
                    FOREIGN KEY(session_id)
                        REFERENCES agent_sessions(session_id)
                        ON DELETE RESTRICT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_traces_session_id
                ON agent_traces(session_id, started_at, trace_id)
                """
            )

    def record(self, trace: AgentTraceRecord) -> None:
        """在同一事务更新会话摘要并追加不可覆盖的请求追踪。"""
        _validate_trace(trace)
        tool_ids_json = json.dumps(
            list(trace.tool_call_ids),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        citation_ids_json = json.dumps(
            list(trace.citation_ids),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_sessions (
                    session_id,
                    created_at,
                    last_trace_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_trace_at = CASE
                        WHEN excluded.last_trace_at > agent_sessions.last_trace_at
                        THEN excluded.last_trace_at
                        ELSE agent_sessions.last_trace_at
                    END
                """,
                (
                    trace.session_id,
                    trace.started_at,
                    trace.completed_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO agent_traces (
                    trace_id,
                    session_id,
                    started_at,
                    completed_at,
                    duration_ms,
                    status,
                    intent,
                    route_reason,
                    tool_call_ids_json,
                    llm_request_id,
                    citation_ids_json,
                    error_code,
                    question_length,
                    interview_record_length
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.trace_id,
                    trace.session_id,
                    trace.started_at,
                    trace.completed_at,
                    trace.duration_ms,
                    trace.status,
                    trace.intent,
                    trace.route_reason,
                    tool_ids_json,
                    trace.llm_request_id,
                    citation_ids_json,
                    trace.error_code,
                    trace.question_length,
                    trace.interview_record_length,
                ),
            )

    def load_records(
        self,
        *,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[AgentTraceRecord, ...]:
        """按会话或追踪 ID 稳定读取安全摘要。"""
        # 延迟导入避免 storage 包初始化时反向组装 application runtime。
        from interview_agent.application.models import AgentTraceRecord

        if session_id is not None and trace_id is not None:
            raise ValueError("Only one trace filter may be provided")
        query = """
            SELECT
                trace_id,
                session_id,
                started_at,
                completed_at,
                duration_ms,
                status,
                intent,
                route_reason,
                tool_call_ids_json,
                llm_request_id,
                citation_ids_json,
                error_code,
                question_length,
                interview_record_length
            FROM agent_traces
        """
        parameters: tuple[str, ...] = ()
        if session_id is not None:
            query += " WHERE session_id = ?"
            parameters = (session_id,)
        elif trace_id is not None:
            query += " WHERE trace_id = ?"
            parameters = (trace_id,)
        query += " ORDER BY started_at, trace_id"

        with self.database.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(
            AgentTraceRecord(
                trace_id=row[0],
                session_id=row[1],
                started_at=row[2],
                completed_at=row[3],
                duration_ms=row[4],
                status=row[5],
                intent=row[6],
                route_reason=row[7],
                tool_call_ids=_decode_string_tuple(row[8], "tool_call_ids"),
                llm_request_id=row[9],
                citation_ids=_decode_string_tuple(row[10], "citation_ids"),
                error_code=row[11],
                question_length=row[12],
                interview_record_length=row[13],
            )
            for row in rows
        )


def _validate_trace(trace: object) -> None:
    """写入前校验身份、计数和有界安全字符串。"""
    from interview_agent.application.models import AgentTraceRecord

    if not isinstance(trace, AgentTraceRecord):
        raise ValueError("trace must be an AgentTraceRecord")
    _require_uuid(trace.trace_id, "trace_id")
    _require_uuid(trace.session_id, "session_id")
    required = (
        trace.started_at,
        trace.completed_at,
        trace.status,
        trace.route_reason,
    )
    if any(not _is_safe_text(value) for value in required):
        raise ValueError("Agent trace fields must be safe non-empty strings")
    optional = (
        trace.intent,
        trace.llm_request_id,
        trace.error_code,
    )
    if any(value is not None and not _is_safe_text(value) for value in optional):
        raise ValueError("Agent trace optional fields must be safe strings")
    if (
        isinstance(trace.duration_ms, bool)
        or not isinstance(trace.duration_ms, int)
        or trace.duration_ms < 0
        or isinstance(trace.question_length, bool)
        or not isinstance(trace.question_length, int)
        or trace.question_length < -1
        or isinstance(trace.interview_record_length, bool)
        or not isinstance(trace.interview_record_length, int)
        or trace.interview_record_length < 0
    ):
        raise ValueError("Agent trace counts are invalid")
    for identity in (*trace.tool_call_ids,):
        _require_uuid(identity, "tool_call_id")
    if len(set(trace.tool_call_ids)) != len(trace.tool_call_ids):
        raise ValueError("Agent trace tool_call_ids must be unique")
    if any(not _is_safe_text(value) for value in trace.citation_ids):
        raise ValueError("Agent trace citation_ids must be safe")


def _require_uuid(value: object, label: str) -> None:
    """只接受规范 UUID，避免任意身份进入索引键。"""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a canonical UUID")
    try:
        normalized = str(UUID(value))
    except ValueError as error:
        raise ValueError(f"{label} must be a canonical UUID") from error
    if normalized != value:
        raise ValueError(f"{label} must be a canonical UUID")


def _is_safe_text(value: object) -> bool:
    """追踪字符串必须有界、单行并可安全编码。"""
    if not (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 512
        and all(ord(character) >= 32 and ord(character) != 127 for character in value)
    ):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _decode_string_tuple(value: str, label: str) -> tuple[str, ...]:
    """恢复字符串数组，拒绝损坏或嵌套 JSON。"""
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise sqlite3.DatabaseError(
            f"Invalid {label} JSON in agent_traces"
        ) from error
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) and item for item in decoded
    ):
        raise sqlite3.DatabaseError(f"Invalid {label} in agent_traces")
    return tuple(decoded)


__all__ = ["SQLiteAgentTraceStore"]
