"""使用本地 Chroma 持久化片段向量和检索所需元数据。"""

from __future__ import annotations

import gc
import json
import math
from collections.abc import Sequence
from numbers import Real
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from interview_agent.retrieval.vector_index import (
    ChunkSearchResult,
    VectorIndexProfile,
    VectorRecord,
    VectorStoreError,
)

DEFAULT_COLLECTION_NAME = "interview_agent_chunks"
CHROMA_OPERATION_BATCH_SIZE = 256


class ChromaVectorStoreError(VectorStoreError):
    """Chroma 初始化、写入、删除或查询失败。"""


class ChromaVectorDataError(ChromaVectorStoreError):
    """Chroma 中保存的检索元数据不完整或已损坏。"""


class ChromaVectorStore:
    """封装 Chroma 细节，并确保 Windows 上可显式释放文件句柄。"""

    def __init__(
        self,
        persistence_path: str | Path,
        *,
        collection_name: str = DEFAULT_COLLECTION_NAME,
    ) -> None:
        normalized_name = collection_name.strip()
        if not normalized_name or "\0" in normalized_name:
            raise ValueError(
                "Chroma collection_name must be non-empty and contain no NUL"
            )

        self.persistence_path = Path(persistence_path).resolve()
        if self.persistence_path.exists() and not self.persistence_path.is_dir():
            raise ValueError("Chroma persistence_path must be a directory")
        self.persistence_path.mkdir(parents=True, exist_ok=True)

        self.collection_name = normalized_name
        self._closed = False
        self._profile: VectorIndexProfile | None = None
        self._collection: Any | None = None
        self._client: Any | None = None

        try:
            # 显式关闭匿名遥测；本地私人知识库不应默认向第三方发送运行信息。
            self._client = chromadb.PersistentClient(
                path=str(self.persistence_path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        except Exception as error:
            raise ChromaVectorStoreError(
                "Failed to open the local Chroma vector store"
            ) from error

    def __enter__(self) -> ChromaVectorStore:
        """允许调用方使用 with 自动关闭 Chroma。"""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """退出 with 时释放 Windows 仍可能占用的索引文件。"""
        self.close()

    def close(self) -> None:
        """幂等关闭 Chroma 客户端，确保临时目录和运行目录可被清理。"""
        if self._closed:
            return
        client = self._client
        # 先断开 Collection；其 Rust binding 不应比共享 Client 活得更久。
        self._collection = None
        self._profile = None
        try:
            if client is not None:
                client.close()
        except Exception as error:
            raise ChromaVectorStoreError(
                "Failed to close the local Chroma vector store"
            ) from error
        finally:
            self._closed = True
            # Chroma 1.5.x 的已关闭 Client 仍保留 Rust `_server` 引用；wrapper
            # 若继续持有 Client，Windows 可能无法及时删除 SQLite 临时文件。
            self._client = None
            del client
            # close() 是低频生命周期操作；显式回收 Rust wrapper，避免 Windows
            # 在测试或应用退出时短暂保留 chroma.sqlite3 文件句柄。
            gc.collect()

    def initialize(self, profile: VectorIndexProfile) -> None:
        """打开或创建使用余弦距离的片段集合。"""
        self._require_open()
        if profile.vector_store != "chroma" or profile.distance_metric != "cosine":
            raise ValueError(
                "ChromaVectorStore requires vector_store='chroma' "
                "and distance_metric='cosine'"
            )

        try:
            # embedding_function=None 可防止 Chroma 自动下载模型或发送正文。
            client = self._client
            if client is None:
                raise ChromaVectorStoreError(
                    "Chroma vector store client is unavailable"
                )
            self._collection = client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=None,
                metadata=_profile_metadata(profile),
                configuration={"hnsw": {"space": "cosine"}},
            )
            self._profile = profile
        except Exception as error:
            raise ChromaVectorStoreError(
                "Failed to initialize the Chroma collection"
            ) from error

    def profile_fingerprint(self) -> str | None:
        """读取集合元数据中的配置指纹；旧集合可能没有该字段。"""
        collection = self._require_collection()
        metadata = collection.metadata or {}
        value = metadata.get("profile_fingerprint")
        return value if isinstance(value, str) else None

    def reset(self, profile: VectorIndexProfile) -> None:
        """删除旧集合并以当前模型维度和距离配置重新创建。"""
        self._require_collection()
        try:
            client = self._client
            if client is None:
                raise ChromaVectorStoreError(
                    "Chroma vector store client is unavailable"
                )
            client.delete_collection(name=self.collection_name)
            self._collection = client.create_collection(
                name=self.collection_name,
                embedding_function=None,
                metadata=_profile_metadata(profile),
                configuration={"hnsw": {"space": "cosine"}},
            )
            self._profile = profile
        except Exception as error:
            raise ChromaVectorStoreError(
                "Failed to rebuild the Chroma collection"
            ) from error

    def count(self) -> int:
        """返回集合中的片段数量。"""
        try:
            return int(self._require_collection().count())
        except ChromaVectorStoreError:
            raise
        except Exception as error:
            raise ChromaVectorStoreError(
                "Failed to count Chroma vector records"
            ) from error

    def delete_documents(self, document_ids: Sequence[str]) -> None:
        """按文档 ID 删除全部旧片段，解决文档缩短后的残留问题。"""
        if any(
            not isinstance(document_id, str) or not document_id
            for document_id in document_ids
        ):
            raise ValueError("document_ids must contain only non-empty strings")
        normalized_ids = tuple(sorted(set(document_ids)))
        if not normalized_ids:
            return

        collection = self._require_collection()
        try:
            for start in range(0, len(normalized_ids), CHROMA_OPERATION_BATCH_SIZE):
                batch = normalized_ids[start : start + CHROMA_OPERATION_BATCH_SIZE]
                where: dict[str, object]
                if len(batch) == 1:
                    where = {"document_id": batch[0]}
                else:
                    where = {"document_id": {"$in": list(batch)}}
                collection.delete(where=where)
        except Exception as error:
            raise ChromaVectorStoreError(
                "Failed to delete Chroma document vectors"
            ) from error

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        """分批按稳定 chunk_id 新增或覆盖向量、正文和引用元数据。"""
        if not records:
            return
        profile = self._require_profile()
        if len({record.chunk_id for record in records}) != len(records):
            raise ValueError("Vector records contain duplicate chunk_id values")

        for record in records:
            if len(record.embedding) != profile.embedding_dimension:
                raise ValueError(
                    "Vector record dimension does not match the active profile"
                )

        collection = self._require_collection()
        try:
            for start in range(0, len(records), CHROMA_OPERATION_BATCH_SIZE):
                batch = records[start : start + CHROMA_OPERATION_BATCH_SIZE]
                collection.upsert(
                    ids=[record.chunk_id for record in batch],
                    embeddings=[list(record.embedding) for record in batch],
                    documents=[record.content for record in batch],
                    metadatas=[_record_metadata(record) for record in batch],
                )
        except Exception as error:
            raise ChromaVectorStoreError(
                "Failed to upsert Chroma vector records"
            ) from error

    def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int,
        source_namespace: str | None = None,
    ) -> tuple[ChunkSearchResult, ...]:
        """用调用方提供的向量查询，并恢复为稳定的可引用结果。"""
        profile = self._require_profile()
        if len(query_embedding) != profile.embedding_dimension:
            raise ValueError(
                "Query embedding dimension does not match the active profile"
            )
        collection = self._require_collection()
        record_count = self.count()
        if record_count == 0:
            return ()

        query_arguments: dict[str, object] = {
            "query_embeddings": [list(query_embedding)],
            "n_results": min(top_k, record_count),
            "include": ["documents", "metadatas", "distances"],
        }
        if source_namespace is not None:
            query_arguments["where"] = {"source_namespace": source_namespace}

        try:
            raw_result = collection.query(**query_arguments)
        except Exception as error:
            raise ChromaVectorStoreError(
                "Failed to query the Chroma vector store"
            ) from error

        results = _decode_query_result(raw_result)
        # Chroma 已按距离排序；这里再以 chunk_id 打破同分并保持测试可重复。
        return tuple(sorted(results, key=lambda item: (-item.score, item.chunk_id)))

    def _require_open(self) -> None:
        """拒绝在显式关闭后继续使用同一个实例。"""
        if self._closed:
            raise ChromaVectorStoreError("Chroma vector store is already closed")

    def _require_collection(self) -> Any:
        """返回已初始化集合，并给出比 AttributeError 更明确的错误。"""
        self._require_open()
        if self._collection is None:
            raise ChromaVectorStoreError(
                "Chroma vector store must be initialized before use"
            )
        return self._collection

    def _require_profile(self) -> VectorIndexProfile:
        """返回当前集合配置。"""
        self._require_collection()
        if self._profile is None:
            raise ChromaVectorStoreError("Chroma vector profile is unavailable")
        return self._profile


def _profile_metadata(profile: VectorIndexProfile) -> dict[str, str | int]:
    """构造不含密钥和本机路径的集合级配置元数据。"""
    return {
        "profile_fingerprint": profile.fingerprint,
        "embedding_model": profile.embedding_model,
        "embedding_dimension": profile.embedding_dimension,
        "vector_store": profile.vector_store,
        "distance_metric": profile.distance_metric,
        "format_version": profile.format_version,
    }


def _record_metadata(record: VectorRecord) -> dict[str, str | int]:
    """把片段引用信息编码为 Chroma 支持的标量元数据。"""
    return {
        "document_id": record.document_id,
        "source_namespace": record.source_namespace,
        "relative_path": record.relative_path.as_posix(),
        "chunk_index": record.chunk_index,
        "heading_path_json": json.dumps(
            list(record.heading_path),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "start_line": record.start_line,
        "end_line": record.end_line,
        "fingerprint": record.fingerprint,
    }


def _decode_query_result(raw_result: dict[str, Any]) -> list[ChunkSearchResult]:
    """校验 Chroma 查询响应，防止损坏元数据变成伪造引用。"""
    ids = _first_result_list(raw_result, "ids")
    documents = _first_result_list(raw_result, "documents")
    metadatas = _first_result_list(raw_result, "metadatas")
    distances = _first_result_list(raw_result, "distances")
    if not (len(ids) == len(documents) == len(metadatas) == len(distances)):
        raise ChromaVectorDataError("Chroma query result columns have unequal lengths")

    results: list[ChunkSearchResult] = []
    for position, chunk_id in enumerate(ids):
        document = documents[position]
        metadata = metadatas[position]
        distance = distances[position]
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ChromaVectorDataError("Chroma query result has an invalid chunk_id")
        if not isinstance(document, str):
            raise ChromaVectorDataError("Chroma query result has invalid content")
        if not isinstance(metadata, dict):
            raise ChromaVectorDataError("Chroma query result has invalid metadata")
        if (
            isinstance(distance, bool)
            or not isinstance(distance, Real)
            or not math.isfinite(float(distance))
        ):
            raise ChromaVectorDataError("Chroma query result has invalid distance")

        relative_path = Path(_require_string(metadata, "relative_path"))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ChromaVectorDataError(
                "Chroma query result has an unsafe relative_path"
            )

        results.append(
            ChunkSearchResult(
                chunk_id=chunk_id,
                document_id=_require_string(metadata, "document_id"),
                source_namespace=_require_string(metadata, "source_namespace"),
                relative_path=relative_path,
                chunk_index=_require_integer(metadata, "chunk_index"),
                heading_path=_decode_heading_path(metadata),
                start_line=_require_integer(metadata, "start_line"),
                end_line=_require_integer(metadata, "end_line"),
                fingerprint=_require_string(metadata, "fingerprint"),
                content=document,
                # 余弦距离越小越相似；转换后分数越大越相关，范围通常为 [-1, 1]。
                score=1.0 - float(distance),
            )
        )
    return results


def _first_result_list(raw_result: dict[str, Any], key: str) -> list[Any]:
    """读取 Chroma 单查询响应的第一组结果。"""
    value = raw_result.get(key)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], list):
        raise ChromaVectorDataError(f"Chroma query result has invalid {key}")
    return value[0]


def _require_string(metadata: dict[str, Any], key: str) -> str:
    """读取必需字符串元数据。"""
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise ChromaVectorDataError(f"Chroma metadata field {key} is invalid")
    return value


def _require_integer(metadata: dict[str, Any], key: str) -> int:
    """读取必需整数元数据，并拒绝 bool 冒充整数。"""
    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChromaVectorDataError(f"Chroma metadata field {key} is invalid")
    return value


def _decode_heading_path(metadata: dict[str, Any]) -> tuple[str, ...]:
    """把标题路径 JSON 恢复为字符串元组。"""
    encoded = _require_string(metadata, "heading_path_json")
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError as error:
        raise ChromaVectorDataError(
            "Chroma heading_path_json is invalid"
        ) from error
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) for item in decoded
    ):
        raise ChromaVectorDataError("Chroma heading_path_json is invalid")
    return tuple(decoded)
