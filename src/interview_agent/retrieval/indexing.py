"""为 Markdown 文档生成稳定索引记录，并计算增量变更计划。"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from interview_agent.retrieval.chunking import (
    DEFAULT_MAX_CHUNK_CHARACTERS,
    MarkdownChunk,
    split_markdown_document,
)
from interview_agent.retrieval.front_matter import (
    reconstruct_document_content,
    separate_front_matter,
)
from interview_agent.retrieval.markdown import MarkdownDocument

# 指纹格式发生不兼容变化时提升此版本，可强制已有文档重新建立索引。
INDEX_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class IndexChunk:
    """待进入后续向量索引的一条片段记录。"""

    chunk_id: str
    document_id: str
    source_namespace: str
    relative_path: Path
    chunk_index: int
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    fingerprint: str

    # 正文只在当前内存流水线中存在；SQLite 状态表不会保存该字段。
    content: str


@dataclass(frozen=True, slots=True)
class IndexDocument:
    """一篇文档当前应有的完整索引状态。"""

    document_id: str
    source_namespace: str
    source_path: Path
    relative_path: Path
    content_fingerprint: str
    index_fingerprint: str
    front_matter_present: bool
    chunks: tuple[IndexChunk, ...]


@dataclass(frozen=True, slots=True)
class StoredDocumentState:
    """SQLite 中用于增量比较的最小文档状态。"""

    document_id: str
    source_namespace: str
    relative_path: Path
    content_fingerprint: str
    index_fingerprint: str
    front_matter_present: bool
    chunk_count: int


@dataclass(frozen=True, slots=True)
class StoredChunkState:
    """SQLite 中保存的片段元数据，不包含私人正文。"""

    chunk_id: str
    document_id: str
    chunk_index: int
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class IndexPlan:
    """当前文件与已保存状态比较后的确定性增量计划。"""

    added: tuple[IndexDocument, ...]
    modified: tuple[IndexDocument, ...]
    unchanged: tuple[IndexDocument, ...]
    deleted: tuple[StoredDocumentState, ...]

    @property
    def change_count(self) -> int:
        """返回需要写入或删除的文档总数。"""
        return len(self.added) + len(self.modified) + len(self.deleted)


def prepare_index_documents(
    documents: Iterable[MarkdownDocument],
    *,
    max_chunk_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS,
    source_namespace: str = "markdown",
) -> list[IndexDocument]:
    """分离 Front Matter、切分文档并生成稳定索引记录。"""
    prepared: list[IndexDocument] = []
    seen_document_ids: set[str] = set()

    for document in documents:
        indexed_document = prepare_index_document(
            document,
            max_chunk_characters=max_chunk_characters,
            source_namespace=source_namespace,
        )
        if indexed_document.document_id in seen_document_ids:
            raise ValueError(
                "Duplicate Markdown document identity: "
                f"{indexed_document.relative_path.as_posix()}"
            )
        seen_document_ids.add(indexed_document.document_id)
        prepared.append(indexed_document)

    return prepared


def prepare_index_document(
    document: MarkdownDocument,
    *,
    max_chunk_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS,
    source_namespace: str = "markdown",
) -> IndexDocument:
    """为单篇文档生成文档指纹、片段指纹和稳定标识。"""
    normalized_namespace = source_namespace.strip()
    if not normalized_namespace or "\0" in normalized_namespace:
        raise ValueError("source_namespace must be non-empty and contain no NUL")

    relative_path_text = _validate_relative_path(document.relative_path)
    parsed_document = separate_front_matter(document)
    raw_content = reconstruct_document_content(parsed_document)
    content_fingerprint = _sha256_text(raw_content)
    document_id = _sha256_text(
        f"document\0{normalized_namespace}\0{relative_path_text}"
    )

    markdown_chunks = split_markdown_document(
        parsed_document,
        max_chunk_characters=max_chunk_characters,
    )
    index_chunks = tuple(
        _prepare_index_chunk(document_id, normalized_namespace, chunk)
        for chunk in markdown_chunks
    )

    # index_fingerprint 同时覆盖原文、切分结果和格式版本；配置或算法改变也可被发现。
    index_fingerprint = _sha256_json(
        {
            "format_version": INDEX_FORMAT_VERSION,
            "content_fingerprint": content_fingerprint,
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "fingerprint": chunk.fingerprint,
                }
                for chunk in index_chunks
            ],
        }
    )

    return IndexDocument(
        document_id=document_id,
        source_namespace=normalized_namespace,
        source_path=parsed_document.source_path,
        relative_path=parsed_document.relative_path,
        content_fingerprint=content_fingerprint,
        index_fingerprint=index_fingerprint,
        front_matter_present=parsed_document.front_matter is not None,
        chunks=index_chunks,
    )


def build_index_plan(
    current_documents: Iterable[IndexDocument],
    stored_documents: Iterable[StoredDocumentState],
) -> IndexPlan:
    """比较当前索引记录和 SQLite 状态，返回新增、修改、未变与删除项。"""
    current_by_id = _unique_current_documents(current_documents)
    stored_by_id = _unique_stored_documents(stored_documents)

    added: list[IndexDocument] = []
    modified: list[IndexDocument] = []
    unchanged: list[IndexDocument] = []

    for document in _sort_index_documents(current_by_id.values()):
        stored = stored_by_id.get(document.document_id)
        if stored is None:
            added.append(document)
        elif stored.index_fingerprint != document.index_fingerprint:
            modified.append(document)
        else:
            unchanged.append(document)

    deleted = [
        state
        for document_id, state in stored_by_id.items()
        if document_id not in current_by_id
    ]
    deleted.sort(
        key=lambda state: _document_sort_key(
            state.source_namespace, state.relative_path
        )
    )

    return IndexPlan(
        added=tuple(added),
        modified=tuple(modified),
        unchanged=tuple(unchanged),
        deleted=tuple(deleted),
    )


def _prepare_index_chunk(
    document_id: str, source_namespace: str, chunk: MarkdownChunk
) -> IndexChunk:
    """把 MarkdownChunk 转换为带稳定 ID 和完整元数据指纹的索引片段。"""
    chunk_id = _sha256_text(f"chunk\0{document_id}\0{chunk.chunk_index}")
    fingerprint = _sha256_json(
        {
            "heading_path": list(chunk.heading_path),
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "content": chunk.content,
        }
    )
    return IndexChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        source_namespace=source_namespace,
        relative_path=chunk.relative_path,
        chunk_index=chunk.chunk_index,
        heading_path=chunk.heading_path,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        fingerprint=fingerprint,
        content=chunk.content,
    )


def _validate_relative_path(relative_path: Path) -> str:
    """稳定 ID 只接受数据源内的规范相对路径。"""
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"Invalid relative Markdown path: {relative_path}")

    normalized = relative_path.as_posix()
    if normalized in {"", "."}:
        raise ValueError(f"Invalid relative Markdown path: {relative_path}")
    return normalized


def _unique_current_documents(
    documents: Iterable[IndexDocument],
) -> dict[str, IndexDocument]:
    """建立当前文档映射，并拒绝会让计划不确定的重复 ID。"""
    result: dict[str, IndexDocument] = {}
    for document in documents:
        if document.document_id in result:
            raise ValueError(f"Duplicate current document ID: {document.document_id}")
        result[document.document_id] = document
    return result


def _unique_stored_documents(
    documents: Iterable[StoredDocumentState],
) -> dict[str, StoredDocumentState]:
    """建立已保存状态映射，并拒绝重复状态。"""
    result: dict[str, StoredDocumentState] = {}
    for document in documents:
        if document.document_id in result:
            raise ValueError(f"Duplicate stored document ID: {document.document_id}")
        result[document.document_id] = document
    return result


def _sort_index_documents(
    documents: Iterable[IndexDocument],
) -> list[IndexDocument]:
    """使用与加载器一致的相对路径规则保持计划顺序稳定。"""
    return sorted(
        documents,
        key=lambda document: _document_sort_key(
            document.source_namespace, document.relative_path
        ),
    )


def _document_sort_key(namespace: str, path: Path) -> tuple[str, str, str, str]:
    """先按数据源命名空间，再按相对路径稳定排序。"""
    path_key = _path_sort_key(path)
    return namespace.casefold(), namespace, *path_key


def _path_sort_key(path: Path) -> tuple[str, str]:
    """先大小写无关排序，再用原始路径打破平局。"""
    text = path.as_posix()
    return text.casefold(), text


def _sha256_text(value: str) -> str:
    """对 UTF-8 文本生成固定 64 位十六进制 SHA-256。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    """先做确定性 JSON 序列化，再生成 SHA-256。"""
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(serialized)
