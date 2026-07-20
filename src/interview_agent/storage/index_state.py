"""使用 SQLite 保存文档和片段的最小增量索引状态。"""

import json
import sqlite3
from pathlib import Path

from interview_agent.retrieval.indexing import (
    IndexDocument,
    IndexPlan,
    StoredChunkState,
    StoredDocumentState,
)
from interview_agent.storage.sqlite import SQLiteDatabase


class SQLiteIndexStateStore:
    """集中管理索引状态表，业务调用方不直接拼接 SQL。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def initialize(self) -> None:
        """幂等创建索引状态表和必要索引。"""
        with self.database.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS index_documents (
                    document_id TEXT PRIMARY KEY,
                    source_namespace TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL
                        CHECK(length(content_fingerprint) = 64),
                    index_fingerprint TEXT NOT NULL
                        CHECK(length(index_fingerprint) = 64),
                    front_matter_present INTEGER NOT NULL
                        CHECK(front_matter_present IN (0, 1)),
                    chunk_count INTEGER NOT NULL CHECK(chunk_count >= 0),
                    indexed_at TEXT NOT NULL,
                    UNIQUE(source_namespace, relative_path)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS index_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
                    heading_path_json TEXT NOT NULL,
                    start_line INTEGER NOT NULL CHECK(start_line > 0),
                    end_line INTEGER NOT NULL CHECK(end_line >= start_line),
                    fingerprint TEXT NOT NULL CHECK(length(fingerprint) = 64),
                    UNIQUE(document_id, chunk_index),
                    FOREIGN KEY(document_id)
                        REFERENCES index_documents(document_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_index_chunks_document_id
                ON index_chunks(document_id)
                """
            )

    def load_document_states(self) -> tuple[StoredDocumentState, ...]:
        """按相对路径稳定读取全部文档状态。"""
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    document_id,
                    source_namespace,
                    relative_path,
                    content_fingerprint,
                    index_fingerprint,
                    front_matter_present,
                    chunk_count
                FROM index_documents
                ORDER BY
                    source_namespace COLLATE NOCASE,
                    source_namespace,
                    relative_path COLLATE NOCASE,
                    relative_path
                """
            ).fetchall()

        return tuple(
            StoredDocumentState(
                document_id=row[0],
                source_namespace=row[1],
                relative_path=Path(row[2]),
                content_fingerprint=row[3],
                index_fingerprint=row[4],
                front_matter_present=bool(row[5]),
                chunk_count=row[6],
            )
            for row in rows
        )

    def load_chunk_states(
        self, document_id: str | None = None
    ) -> tuple[StoredChunkState, ...]:
        """读取全部或指定文档的片段元数据，不返回片段正文。"""
        query = """
            SELECT
                chunk_id,
                document_id,
                chunk_index,
                heading_path_json,
                start_line,
                end_line,
                fingerprint
            FROM index_chunks
        """
        parameters: tuple[str, ...] = ()
        if document_id is not None:
            query += " WHERE document_id = ?"
            parameters = (document_id,)
        query += " ORDER BY document_id, chunk_index"

        with self.database.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()

        return tuple(
            StoredChunkState(
                chunk_id=row[0],
                document_id=row[1],
                chunk_index=row[2],
                heading_path=_decode_heading_path(row[3]),
                start_line=row[4],
                end_line=row[5],
                fingerprint=row[6],
            )
            for row in rows
        )

    def apply_plan(self, plan: IndexPlan) -> None:
        """在单个事务中删除失效状态，并替换新增或修改的文档状态。"""
        write_documents = (*plan.added, *plan.modified)
        _validate_plan_documents(write_documents)

        with self.database.connection() as connection:
            # 先删除失效文档，外键级联会同步删除其片段状态。
            connection.executemany(
                "DELETE FROM index_documents WHERE document_id = ?",
                ((state.document_id,) for state in plan.deleted),
            )

            for document in write_documents:
                self._replace_document(connection, document)

    @staticmethod
    def _replace_document(
        connection: sqlite3.Connection, document: IndexDocument
    ) -> None:
        """更新文档元数据，并用当前片段集合完整替换旧片段。"""
        connection.execute(
            """
            INSERT INTO index_documents (
                document_id,
                source_namespace,
                relative_path,
                content_fingerprint,
                index_fingerprint,
                front_matter_present,
                chunk_count,
                indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(document_id) DO UPDATE SET
                source_namespace = excluded.source_namespace,
                relative_path = excluded.relative_path,
                content_fingerprint = excluded.content_fingerprint,
                index_fingerprint = excluded.index_fingerprint,
                front_matter_present = excluded.front_matter_present,
                chunk_count = excluded.chunk_count,
                indexed_at = excluded.indexed_at
            """,
            (
                document.document_id,
                document.source_namespace,
                document.relative_path.as_posix(),
                document.content_fingerprint,
                document.index_fingerprint,
                int(document.front_matter_present),
                len(document.chunks),
            ),
        )

        # 文档变化时先删除旧片段，避免章节减少后残留失效记录。
        connection.execute(
            "DELETE FROM index_chunks WHERE document_id = ?",
            (document.document_id,),
        )
        connection.executemany(
            """
            INSERT INTO index_chunks (
                chunk_id,
                document_id,
                chunk_index,
                heading_path_json,
                start_line,
                end_line,
                fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.chunk_index,
                    json.dumps(
                        list(chunk.heading_path),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    chunk.start_line,
                    chunk.end_line,
                    chunk.fingerprint,
                )
                for chunk in document.chunks
            ),
        )


def _validate_plan_documents(documents: tuple[IndexDocument, ...]) -> None:
    """写入前检查文档和片段关系，防止不一致状态进入 SQLite。"""
    seen_document_ids: set[str] = set()
    for document in documents:
        if document.document_id in seen_document_ids:
            raise ValueError(f"Duplicate plan document ID: {document.document_id}")
        seen_document_ids.add(document.document_id)

        if len(document.chunks) != len(
            {chunk.chunk_index for chunk in document.chunks}
        ):
            raise ValueError(
                f"Duplicate chunk index for document: {document.document_id}"
            )
        if any(
            chunk.document_id != document.document_id
            or chunk.source_namespace != document.source_namespace
            for chunk in document.chunks
        ):
            raise ValueError(
                f"Chunk parent metadata mismatch: {document.document_id}"
            )


def _decode_heading_path(value: str) -> tuple[str, ...]:
    """把 SQLite 中的 JSON 标题路径恢复为不可变元组。"""
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise sqlite3.DatabaseError(
            "Invalid heading path JSON in index_chunks"
        ) from error
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) for item in decoded
    ):
        raise sqlite3.DatabaseError("Invalid heading path in index_chunks")
    return tuple(decoded)
