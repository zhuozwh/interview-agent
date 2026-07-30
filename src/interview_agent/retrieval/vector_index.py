"""编排 Embedding、Chroma 向量状态与 SQLite 增量状态。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from interview_agent.retrieval.embedding import (
    EmbeddingProvider,
    embed_query,
    embed_texts,
    validate_embedding_provider,
)
from interview_agent.retrieval.indexing import IndexDocument, IndexPlan

VECTOR_INDEX_FORMAT_VERSION = 1
DEFAULT_EMBEDDING_BATCH_SIZE = 64
MAX_SEARCH_RESULTS = 100


class VectorIndexError(RuntimeError):
    """向量索引同步或检索错误的基类。"""


class VectorStoreError(VectorIndexError):
    """具体向量存储初始化、写入或查询失败。"""


class VectorIndexProfileError(VectorIndexError):
    """当前 Embedding 配置与已建立的索引不一致。"""


class VectorSearchInputError(VectorIndexError, ValueError):
    """向量检索问题或返回数量参数无效。"""


@dataclass(frozen=True, slots=True)
class VectorIndexProfile:
    """描述一套向量能否安全复用的全部关键配置。"""

    embedding_model: str
    embedding_dimension: int
    vector_store: str = "chroma"
    distance_metric: str = "cosine"
    format_version: int = VECTOR_INDEX_FORMAT_VERSION

    def __post_init__(self) -> None:
        """在进入 SQLite 或 Chroma 前统一校验并规范化配置。"""
        normalized_model = self.embedding_model.strip()
        normalized_store = self.vector_store.strip()
        normalized_metric = self.distance_metric.strip()
        if not normalized_model or "\0" in normalized_model:
            raise ValueError("embedding_model must be non-empty and contain no NUL")
        if (
            isinstance(self.embedding_dimension, bool)
            or not isinstance(self.embedding_dimension, int)
            or self.embedding_dimension <= 0
        ):
            raise ValueError("embedding_dimension must be a positive integer")
        if not normalized_store or "\0" in normalized_store:
            raise ValueError("vector_store must be non-empty and contain no NUL")
        if not normalized_metric or "\0" in normalized_metric:
            raise ValueError("distance_metric must be non-empty and contain no NUL")
        if (
            isinstance(self.format_version, bool)
            or not isinstance(self.format_version, int)
            or self.format_version <= 0
        ):
            raise ValueError("format_version must be a positive integer")

        object.__setattr__(self, "embedding_model", normalized_model)
        object.__setattr__(self, "vector_store", normalized_store)
        object.__setattr__(self, "distance_metric", normalized_metric)

    @property
    def fingerprint(self) -> str:
        """返回可同时保存到 SQLite 和 Chroma 的稳定配置指纹。"""
        serialized = json.dumps(
            {
                "distance_metric": self.distance_metric,
                "embedding_dimension": self.embedding_dimension,
                "embedding_model": self.embedding_model,
                "format_version": self.format_version,
                "vector_store": self.vector_store,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """准备写入向量存储的一条完整片段记录。"""

    chunk_id: str
    document_id: str
    source_namespace: str
    relative_path: Path
    chunk_index: int
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    fingerprint: str
    content: str
    embedding: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ChunkSearchResult:
    """向上层返回的可引用检索片段，不暴露本机绝对路径。"""

    chunk_id: str
    document_id: str
    source_namespace: str
    relative_path: Path
    chunk_index: int
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    fingerprint: str
    content: str
    score: float


@dataclass(frozen=True, slots=True)
class VectorSyncReport:
    """一次增量向量同步的可观察摘要。"""

    profile_rebuilt: bool
    embedded_document_count: int
    embedded_chunk_count: int
    deleted_document_count: int
    unchanged_document_count: int


class VectorStore(Protocol):
    """向量同步和查询真正需要的最小存储边界。"""

    def initialize(self, profile: VectorIndexProfile) -> None:
        """打开或创建当前集合。"""

    def profile_fingerprint(self) -> str | None:
        """返回集合记录的配置指纹。"""

    def reset(self, profile: VectorIndexProfile) -> None:
        """清空集合并以新配置重新创建。"""

    def count(self) -> int:
        """返回当前集合的向量条数。"""

    def delete_documents(self, document_ids: Sequence[str]) -> None:
        """删除指定文档的全部片段。"""

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        """按 chunk_id 新增或覆盖片段。"""

    def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int,
        source_namespace: str | None = None,
    ) -> tuple[ChunkSearchResult, ...]:
        """查询最相似的片段。"""


class VectorIndexStateStore(Protocol):
    """向量流程对 SQLite 索引状态仓储的最小依赖。"""

    def load_vector_profile(self) -> VectorIndexProfile | None:
        """读取上次成功同步的配置。"""

    def apply_plan(
        self,
        plan: IndexPlan,
        *,
        vector_profile: VectorIndexProfile | None = None,
    ) -> None:
        """提交文档状态，并可在同一事务中提交向量配置。"""


def build_vector_index_profile(
    provider: EmbeddingProvider,
) -> VectorIndexProfile:
    """从供应方公开身份构建可持久化的索引配置。"""
    model_name, dimension = validate_embedding_provider(provider)
    return VectorIndexProfile(
        embedding_model=model_name,
        embedding_dimension=dimension,
    )


def synchronize_vector_index(
    plan: IndexPlan,
    *,
    embedding_provider: EmbeddingProvider,
    vector_store: VectorStore,
    state_store: VectorIndexStateStore,
    batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
) -> VectorSyncReport:
    """先完成向量副作用，再原子提交 SQLite 状态，支持失败后幂等重试。"""
    profile = build_vector_index_profile(embedding_provider)
    vector_store.initialize(profile)

    expected_chunk_count = sum(
        len(document.chunks)
        for document in (*plan.added, *plan.modified, *plan.unchanged)
    )
    stored_profile = state_store.load_vector_profile()

    # 没有文档变化时，数量不一致通常表示向量目录被手工删除或上次写入不完整。
    count_mismatch = (
        plan.change_count == 0 and vector_store.count() != expected_chunk_count
    )
    profile_rebuilt = (
        stored_profile != profile
        or vector_store.profile_fingerprint() != profile.fingerprint
        or count_mismatch
    )
    effective_plan = _force_full_rebuild(plan) if profile_rebuilt else plan
    write_documents = _sorted_documents(
        (*effective_plan.added, *effective_plan.modified)
    )

    chunks = [
        chunk
        for document in write_documents
        for chunk in sorted(document.chunks, key=lambda item: item.chunk_index)
    ]
    embeddings = embed_texts(
        embedding_provider,
        tuple(chunk.content for chunk in chunks),
        batch_size=batch_size,
    )
    records = tuple(
        VectorRecord(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            source_namespace=chunk.source_namespace,
            relative_path=chunk.relative_path,
            chunk_index=chunk.chunk_index,
            heading_path=chunk.heading_path,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            fingerprint=chunk.fingerprint,
            content=chunk.content,
            embedding=embedding,
        )
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    )

    if profile_rebuilt:
        # 先生成全部向量再重置旧集合，供应方失败时不会破坏现有索引。
        vector_store.reset(profile)
    else:
        # 新增项也先清理同 ID 文档，可修复“向量成功、SQLite 失败”后的重试残留。
        vector_store.delete_documents(
            tuple(
                document.document_id
                for document in (*effective_plan.added, *effective_plan.modified)
            )
        )
        vector_store.delete_documents(
            tuple(document.document_id for document in effective_plan.deleted)
        )

    vector_store.upsert(records)

    # 只有所有向量操作都成功后，SQLite 才推进到相同文档状态和配置。
    state_store.apply_plan(effective_plan, vector_profile=profile)
    return VectorSyncReport(
        profile_rebuilt=profile_rebuilt,
        embedded_document_count=len(write_documents),
        embedded_chunk_count=len(records),
        deleted_document_count=len(effective_plan.deleted),
        unchanged_document_count=len(effective_plan.unchanged),
    )


def search_chunks(
    query: str,
    *,
    top_k: int,
    embedding_provider: EmbeddingProvider,
    vector_store: VectorStore,
    state_store: VectorIndexStateStore,
    source_namespace: str | None = None,
) -> tuple[ChunkSearchResult, ...]:
    """把问题转换为向量，并返回带来源路径、标题和原文行号的相关片段。"""
    if not isinstance(query, str) or not query.strip():
        raise VectorSearchInputError("query must be a non-empty string")
    if (
        isinstance(top_k, bool)
        or not isinstance(top_k, int)
        or not 1 <= top_k <= MAX_SEARCH_RESULTS
    ):
        raise VectorSearchInputError(
            f"top_k must be an integer between 1 and {MAX_SEARCH_RESULTS}"
        )

    normalized_namespace: str | None = None
    if source_namespace is not None:
        normalized_namespace = source_namespace.strip()
        if not normalized_namespace or "\0" in normalized_namespace:
            raise VectorSearchInputError(
                "source_namespace must be non-empty and contain no NUL"
            )

    profile = build_vector_index_profile(embedding_provider)
    stored_profile = state_store.load_vector_profile()
    if stored_profile != profile:
        raise VectorIndexProfileError(
            "Vector index is not ready for the current embedding profile"
        )

    vector_store.initialize(profile)
    if vector_store.profile_fingerprint() != profile.fingerprint:
        raise VectorIndexProfileError(
            "Vector store profile does not match the SQLite index state"
        )
    if vector_store.count() == 0:
        return ()

    query_embedding = embed_query(embedding_provider, query)
    return vector_store.search(
        query_embedding,
        top_k=top_k,
        source_namespace=normalized_namespace,
    )


def _force_full_rebuild(plan: IndexPlan) -> IndexPlan:
    """配置变化时把原本未变化的文档也纳入重新 Embedding。"""
    return IndexPlan(
        added=plan.added,
        modified=tuple(
            _sorted_documents((*plan.modified, *plan.unchanged))
        ),
        unchanged=(),
        deleted=plan.deleted,
    )


def _sorted_documents(
    documents: Sequence[IndexDocument],
) -> list[IndexDocument]:
    """按命名空间和相对路径保持写入及 Embedding 调用顺序稳定。"""
    return sorted(
        documents,
        key=lambda document: (
            document.source_namespace.casefold(),
            document.source_namespace,
            document.relative_path.as_posix().casefold(),
            document.relative_path.as_posix(),
        ),
    )
