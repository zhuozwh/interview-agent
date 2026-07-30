"""使用 SQLite 保存不含敏感正文的 Tool 调用追踪。"""

from __future__ import annotations

import json
import math
import sqlite3

from interview_agent.storage.sqlite import SQLiteDatabase
from interview_agent.tools.models import ToolTraceRecord, TraceParameter


class SQLiteToolTraceStore:
    """集中管理 Tool 追踪表和反序列化校验。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def initialize(self) -> None:
        """幂等创建 Tool 追踪表和 trace_id 查询索引。"""
        with self.database.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_traces (
                    tool_call_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                    status TEXT NOT NULL,
                    result_count INTEGER NOT NULL CHECK(result_count >= 0),
                    parameters_json TEXT NOT NULL,
                    result_ids_json TEXT NOT NULL,
                    error_code TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tool_traces_trace_id
                ON tool_traces(trace_id, started_at, tool_call_id)
                """
            )

    def record(self, trace: ToolTraceRecord) -> None:
        """写入一条追踪；重复 tool_call_id 明确报错而不是覆盖审计历史。"""
        _validate_trace(trace)
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO tool_traces (
                    tool_call_id,
                    trace_id,
                    tool_name,
                    started_at,
                    completed_at,
                    duration_ms,
                    status,
                    result_count,
                    parameters_json,
                    result_ids_json,
                    error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.tool_call_id,
                    trace.trace_id,
                    trace.tool_name,
                    trace.started_at,
                    trace.completed_at,
                    trace.duration_ms,
                    trace.status,
                    trace.result_count,
                    json.dumps(
                        dict(trace.parameters),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        list(trace.result_ids),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    trace.error_code,
                ),
            )

    def load_records(
        self,
        trace_id: str | None = None,
    ) -> tuple[ToolTraceRecord, ...]:
        """按时间稳定读取全部记录或一次请求关联的记录。"""
        query = """
            SELECT
                tool_call_id,
                trace_id,
                tool_name,
                started_at,
                completed_at,
                duration_ms,
                status,
                result_count,
                parameters_json,
                result_ids_json,
                error_code
            FROM tool_traces
        """
        parameters: tuple[str, ...] = ()
        if trace_id is not None:
            query += " WHERE trace_id = ?"
            parameters = (trace_id,)
        query += " ORDER BY started_at, tool_call_id"

        with self.database.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()

        return tuple(
            ToolTraceRecord(
                tool_call_id=row[0],
                trace_id=row[1],
                tool_name=row[2],
                started_at=row[3],
                completed_at=row[4],
                duration_ms=row[5],
                status=row[6],
                result_count=row[7],
                parameters=_decode_parameters(row[8]),
                result_ids=_decode_result_ids(row[9]),
                error_code=row[10],
            )
            for row in rows
        )


def _validate_trace(trace: ToolTraceRecord) -> None:
    """写入前拒绝缺失身份、负数计数或重复参数键。"""
    required_strings = (
        trace.tool_call_id,
        trace.trace_id,
        trace.tool_name,
        trace.started_at,
        trace.completed_at,
        trace.status,
    )
    if any(not value or "\0" in value for value in required_strings):
        raise ValueError("Tool trace identity and status fields must be non-empty")
    if trace.duration_ms < 0 or trace.result_count < 0:
        raise ValueError("Tool trace counts must not be negative")
    if trace.result_count != len(trace.result_ids):
        raise ValueError("Tool trace result_count does not match result_ids")
    keys = [key for key, _ in trace.parameters]
    if len(keys) != len(set(keys)) or any(not key for key in keys):
        raise ValueError("Tool trace parameter keys must be unique and non-empty")


def _decode_parameters(value: str) -> tuple[tuple[str, TraceParameter], ...]:
    """恢复参数摘要，并拒绝嵌套对象进入通用追踪模型。"""
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise sqlite3.DatabaseError(
            "Invalid parameters JSON in tool_traces"
        ) from error
    if not isinstance(decoded, dict):
        raise sqlite3.DatabaseError("Invalid parameters in tool_traces")

    parameters: list[tuple[str, TraceParameter]] = []
    for key, item in decoded.items():
        if not isinstance(key, str) or not isinstance(item, (str, int, float, bool)):
            raise sqlite3.DatabaseError("Invalid parameters in tool_traces")
        if isinstance(item, float) and not math.isfinite(item):
            raise sqlite3.DatabaseError("Invalid parameters in tool_traces")
        parameters.append((key, item))
    return tuple(sorted(parameters))


def _decode_result_ids(value: str) -> tuple[str, ...]:
    """恢复本次实际返回的引用 ID。"""
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise sqlite3.DatabaseError(
            "Invalid result IDs JSON in tool_traces"
        ) from error
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) and item for item in decoded
    ):
        raise sqlite3.DatabaseError("Invalid result IDs in tool_traces")
    return tuple(decoded)
