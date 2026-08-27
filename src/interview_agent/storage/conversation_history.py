"""使用 SQLite 保存有明确生命周期的本地聊天正文。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3
from uuid import UUID

from interview_agent.application.history_models import (
    HistoryCitation,
    HistorySession,
    HistorySessionSummary,
    HistoryTurn,
)
from interview_agent.storage.sqlite import SQLiteDatabase


class SQLiteConversationHistoryStore:
    """集中管理聊天正文、展示引用和确定性清理策略。"""

    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        retention_days: int,
        max_sessions: int,
        max_turns_per_session: int,
    ) -> None:
        self.database = database
        self.retention_days = retention_days
        self.max_sessions = max_sessions
        self.max_turns_per_session = max_turns_per_session
        _validate_limits(retention_days, max_sessions, max_turns_per_session)

    def initialize(self) -> None:
        """幂等创建独立历史表，不改变既有 Phase 2 审计表。"""
        with self.database.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_turns (
                    trace_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    question_text TEXT NOT NULL,
                    answer_text TEXT,
                    status TEXT NOT NULL,
                    intent TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    confidence TEXT,
                    citations_json TEXT NOT NULL,
                    follow_up_questions_json TEXT NOT NULL,
                    FOREIGN KEY(session_id)
                        REFERENCES chat_sessions(session_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_turns_session
                ON chat_turns(session_id, created_at, trace_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated
                ON chat_sessions(updated_at, session_id)
                """
            )
            # 索引来自列表和单会话查询的真实模式；初始化后更新查询规划统计。
            connection.execute("PRAGMA optimize")

    def record_turn(self, turn: HistoryTurn) -> None:
        """原子追加一轮，并只保留该会话最近的有限轮次。"""
        _validate_turn(turn)
        citations_json = json.dumps(
            [_citation_to_dict(citation) for citation in turn.citations],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        follow_ups_json = json.dumps(
            list(turn.follow_up_questions),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        title = _title_from_question(turn.question)
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO chat_sessions (
                    session_id, title, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_at = CASE
                        WHEN excluded.updated_at > chat_sessions.updated_at
                        THEN excluded.updated_at
                        ELSE chat_sessions.updated_at
                    END
                """,
                (
                    turn.session_id,
                    title,
                    turn.created_at,
                    turn.created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO chat_turns (
                    trace_id,
                    session_id,
                    created_at,
                    question_text,
                    answer_text,
                    status,
                    intent,
                    error_code,
                    error_message,
                    confidence,
                    citations_json,
                    follow_up_questions_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn.trace_id,
                    turn.session_id,
                    turn.created_at,
                    turn.question,
                    turn.answer,
                    turn.status,
                    turn.intent,
                    turn.error_code,
                    turn.error_message,
                    turn.confidence,
                    citations_json,
                    follow_ups_json,
                ),
            )
            connection.execute(
                """
                DELETE FROM chat_turns
                WHERE session_id = ?
                  AND trace_id NOT IN (
                      SELECT trace_id
                      FROM chat_turns
                      WHERE session_id = ?
                      ORDER BY created_at DESC, trace_id DESC
                      LIMIT ?
                  )
                """,
                (
                    turn.session_id,
                    turn.session_id,
                    self.max_turns_per_session,
                ),
            )
        self.prune()

    def list_sessions(self) -> tuple[HistorySessionSummary, ...]:
        """清理后返回最近更新优先的会话摘要。"""
        self.prune()
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    sessions.session_id,
                    sessions.title,
                    sessions.created_at,
                    sessions.updated_at,
                    COUNT(turns.trace_id)
                FROM chat_sessions AS sessions
                JOIN chat_turns AS turns
                  ON turns.session_id = sessions.session_id
                GROUP BY sessions.session_id
                ORDER BY sessions.updated_at DESC, sessions.session_id DESC
                """
            ).fetchall()
        return tuple(_summary_from_row(row) for row in rows)

    def load_session(self, session_id: str) -> HistorySession | None:
        """读取一个会话及其当前保留的全部轮次。"""
        _require_uuid(session_id, "session_id")
        self.prune()
        with self.database.connection() as connection:
            summary_row = connection.execute(
                """
                SELECT
                    sessions.session_id,
                    sessions.title,
                    sessions.created_at,
                    sessions.updated_at,
                    COUNT(turns.trace_id)
                FROM chat_sessions AS sessions
                JOIN chat_turns AS turns
                  ON turns.session_id = sessions.session_id
                WHERE sessions.session_id = ?
                GROUP BY sessions.session_id
                """,
                (session_id,),
            ).fetchone()
            if summary_row is None:
                return None
            rows = connection.execute(
                """
                SELECT
                    trace_id,
                    session_id,
                    created_at,
                    question_text,
                    answer_text,
                    status,
                    intent,
                    error_code,
                    error_message,
                    confidence,
                    citations_json,
                    follow_up_questions_json
                FROM chat_turns
                WHERE session_id = ?
                ORDER BY created_at, trace_id
                """,
                (session_id,),
            ).fetchall()
        return HistorySession(
            summary=_summary_from_row(summary_row),
            turns=tuple(_turn_from_row(row) for row in rows),
        )

    def delete_session(self, session_id: str) -> bool:
        """级联删除一个会话的聊天正文和展示引用。"""
        _require_uuid(session_id, "session_id")
        with self.database.connection() as connection:
            cursor = connection.execute(
                "DELETE FROM chat_sessions WHERE session_id = ?",
                (session_id,),
            )
            return cursor.rowcount > 0

    def clear(self) -> int:
        """删除全部聊天正文；既有无正文审计追踪保持独立。"""
        with self.database.connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM chat_sessions"
            ).fetchone()[0]
            connection.execute("DELETE FROM chat_sessions")
        return int(count)

    def prune(self) -> int:
        """删除过期会话及超出总量上限的最旧会话。"""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        ).isoformat()
        with self.database.connection() as connection:
            expired = connection.execute(
                "DELETE FROM chat_sessions WHERE updated_at < ?",
                (cutoff,),
            ).rowcount
            overflow = connection.execute(
                """
                DELETE FROM chat_sessions
                WHERE session_id IN (
                    SELECT session_id
                    FROM chat_sessions
                    ORDER BY updated_at DESC, session_id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self.max_sessions,),
            ).rowcount
        return max(0, expired) + max(0, overflow)


def _validate_limits(
    retention_days: object,
    max_sessions: object,
    max_turns_per_session: object,
) -> None:
    """防止直接构造存储时绕过 Settings 的数量边界。"""
    limits = (
        (retention_days, 1, 3650, "retention_days"),
        (max_sessions, 1, 1000, "max_sessions"),
        (max_turns_per_session, 1, 1000, "max_turns_per_session"),
    )
    for value, minimum, maximum, label in limits:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise ValueError(f"{label} is outside the supported range")


def _validate_turn(turn: object) -> None:
    """限制持久化正文和展示字段，拒绝损坏或无界数据。"""
    if not isinstance(turn, HistoryTurn):
        raise ValueError("turn must be a HistoryTurn")
    _require_uuid(turn.trace_id, "trace_id")
    _require_uuid(turn.session_id, "session_id")
    _require_text(turn.created_at, "created_at", 128)
    _require_text(turn.question, "question", 4000)
    _require_text(turn.status, "status", 128)
    if turn.answer is not None:
        _require_text(turn.answer, "answer", 20_000)
    for label, value in (
        ("intent", turn.intent),
        ("error_code", turn.error_code),
        ("error_message", turn.error_message),
        ("confidence", turn.confidence),
    ):
        if value is not None:
            _require_text(value, label, 1024)
    if len(turn.citations) > 10 or len(turn.follow_up_questions) > 10:
        raise ValueError("History collections exceed the supported limit")
    for citation in turn.citations:
        _validate_citation(citation)
    for question in turn.follow_up_questions:
        _require_text(question, "follow_up_question", 1000)


def _validate_citation(citation: object) -> None:
    """引用只允许安全相对路径和有限定位元数据。"""
    if not isinstance(citation, HistoryCitation):
        raise ValueError("citation must be a HistoryCitation")
    for label, value, limit in (
        ("citation_id", citation.citation_id, 64),
        ("source_namespace", citation.source_namespace, 64),
        ("relative_path", citation.relative_path, 1024),
    ):
        _require_text(value, label, limit)
    path = citation.relative_path.replace("\\", "/")
    if path.startswith("/") or ":" in path or ".." in path.split("/"):
        raise ValueError("History citation path must stay relative")
    if len(citation.heading_path) > 16:
        raise ValueError("History citation heading path is too deep")
    for heading in citation.heading_path:
        _require_text(heading, "heading", 512)
    if (
        isinstance(citation.start_line, bool)
        or not isinstance(citation.start_line, int)
        or citation.start_line < 1
        or isinstance(citation.end_line, bool)
        or not isinstance(citation.end_line, int)
        or citation.end_line < citation.start_line
        or isinstance(citation.score, bool)
        or not isinstance(citation.score, (int, float))
        or not math.isfinite(citation.score)
        or not -1.0 <= citation.score <= 1.0
    ):
        raise ValueError("History citation location or score is invalid")


def _require_text(value: object, label: str, maximum: int) -> None:
    """正文允许换行，但必须是非空、有效且有界的 UTF-8。"""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be safe bounded text")
    if "\0" in value:
        raise ValueError(f"{label} must not contain NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be valid UTF-8") from error


def _require_uuid(value: object, label: str) -> None:
    """历史主键只接受规范 UUID。"""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a canonical UUID")
    try:
        normalized = str(UUID(value))
    except ValueError as error:
        raise ValueError(f"{label} must be a canonical UUID") from error
    if normalized != value:
        raise ValueError(f"{label} must be a canonical UUID")


def _title_from_question(question: str) -> str:
    """用首问生成单行短标题，避免侧边栏泄漏过多正文。"""
    normalized = " ".join(question.split())
    return normalized if len(normalized) <= 48 else f"{normalized[:47]}…"


def _citation_to_dict(citation: HistoryCitation) -> dict[str, object]:
    """将引用编码为稳定、无内部片段标识的 JSON。"""
    return {
        "citation_id": citation.citation_id,
        "source_namespace": citation.source_namespace,
        "relative_path": citation.relative_path,
        "heading_path": list(citation.heading_path),
        "start_line": citation.start_line,
        "end_line": citation.end_line,
        "score": citation.score,
    }


def _summary_from_row(row: tuple[object, ...]) -> HistorySessionSummary:
    """把 SQLite 会话行恢复为不可变摘要。"""
    return HistorySessionSummary(
        session_id=str(row[0]),
        title=str(row[1]),
        created_at=str(row[2]),
        updated_at=str(row[3]),
        turn_count=int(row[4]),
    )


def _turn_from_row(row: tuple[object, ...]) -> HistoryTurn:
    """解码一轮历史，并把损坏 JSON 映射为数据库错误。"""
    citations_raw = _decode_json_list(str(row[10]), "citations")
    follow_ups_raw = _decode_json_list(
        str(row[11]), "follow_up_questions"
    )
    try:
        citations = tuple(
            HistoryCitation(
                citation_id=item["citation_id"],
                source_namespace=item["source_namespace"],
                relative_path=item["relative_path"],
                heading_path=tuple(item["heading_path"]),
                start_line=item["start_line"],
                end_line=item["end_line"],
                score=item["score"],
            )
            for item in citations_raw
            if isinstance(item, dict)
        )
    except (KeyError, TypeError, ValueError) as error:
        raise sqlite3.DatabaseError("Invalid citations in chat_turns") from error
    if len(citations) != len(citations_raw) or not all(
        isinstance(item, str) and item for item in follow_ups_raw
    ):
        raise sqlite3.DatabaseError("Invalid history collections")
    turn = HistoryTurn(
        trace_id=str(row[0]),
        session_id=str(row[1]),
        created_at=str(row[2]),
        question=str(row[3]),
        answer=str(row[4]) if row[4] is not None else None,
        status=str(row[5]),
        intent=str(row[6]) if row[6] is not None else None,
        error_code=str(row[7]) if row[7] is not None else None,
        error_message=str(row[8]) if row[8] is not None else None,
        confidence=str(row[9]) if row[9] is not None else None,
        citations=citations,
        follow_up_questions=tuple(follow_ups_raw),
    )
    try:
        _validate_turn(turn)
    except ValueError as error:
        raise sqlite3.DatabaseError("Invalid persisted chat turn") from error
    return turn


def _decode_json_list(value: str, label: str) -> list[object]:
    """只接受顶层数组，拒绝损坏或嵌套协议替换。"""
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise sqlite3.DatabaseError(f"Invalid {label} JSON") from error
    if not isinstance(decoded, list):
        raise sqlite3.DatabaseError(f"Invalid {label} JSON")
    return decoded


__all__ = ["SQLiteConversationHistoryStore"]
