"""提供只读、受限且可追踪的面试笔记检索 Tool。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import math
from numbers import Real
from time import perf_counter_ns
from uuid import UUID, uuid4

from interview_agent.retrieval import (
    ChunkSearchResult,
    EmbeddingError,
    EmbeddingProvider,
    VectorIndexError,
    VectorIndexProfileError,
    VectorIndexStateStore,
    VectorSearchInputError,
    VectorStore,
    VectorStoreError,
    search_chunks,
)
from interview_agent.tools.models import ToolTraceRecord, ToolTraceStore

SEARCH_NOTES_TOOL_NAME = "search_notes"
DEFAULT_TOP_K = 5
MAX_TOP_K = 10
MAX_QUERY_CHARACTERS = 500
MAX_TOTAL_CHARACTERS = 20_000
NOTES_SOURCE_NAMESPACE = "notes"


class SearchNotesStatus(StrEnum):
    """调用方可稳定判断的 Tool 结果状态。"""

    SUCCESS = "success"
    NO_RESULTS = "no_results"
    INVALID_INPUT = "invalid_input"
    INDEX_NOT_READY = "index_not_ready"
    TIMEOUT = "timeout"
    EMBEDDING_ERROR = "embedding_error"
    STORAGE_ERROR = "storage_error"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class SearchNotesRequest:
    """search_notes 的显式输入结构。"""

    query: str
    top_k: int = DEFAULT_TOP_K


@dataclass(frozen=True, slots=True)
class SearchNotesEvidence:
    """Tool 返回的一条有来源、可定位且长度受控的证据。"""

    rank: int
    chunk_id: str
    document_id: str
    source_type: str
    source_namespace: str
    relative_path: str
    heading_path: tuple[str, ...]
    start_line: int
    end_line: int
    fingerprint: str
    content: str
    content_truncated: bool
    score: float


@dataclass(frozen=True, slots=True)
class SearchNotesError:
    """不会暴露第三方异常和私人正文的稳定错误结构。"""

    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class SearchNotesResponse:
    """search_notes 的稳定输出结构。"""

    tool_name: str
    tool_call_id: str
    trace_id: str
    status: SearchNotesStatus
    results: tuple[SearchNotesEvidence, ...]
    error: SearchNotesError | None
    duration_ms: int


class SearchNotesTool:
    """只检索固定 notes 命名空间，不读取文件或生成最终回答。"""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        state_store: VectorIndexStateStore,
        trace_store: ToolTraceStore,
        min_score: float = 0.45,
        max_total_characters: int = 6000,
    ) -> None:
        if (
            isinstance(min_score, bool)
            or not isinstance(min_score, Real)
            or not math.isfinite(float(min_score))
            or not -1.0 <= float(min_score) <= 1.0
        ):
            raise ValueError("min_score must be between -1 and 1")
        if (
            isinstance(max_total_characters, bool)
            or not isinstance(max_total_characters, int)
            or not 1 <= max_total_characters <= MAX_TOTAL_CHARACTERS
        ):
            raise ValueError(
                "max_total_characters must be an integer between "
                f"1 and {MAX_TOTAL_CHARACTERS}"
            )

        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.state_store = state_store
        self.trace_store = trace_store
        self.min_score = float(min_score)
        self.max_total_characters = max_total_characters
        # Tool 名称决定数据边界，调用方不能把 search_notes 改造成任意数据源查询。
        self.source_namespace = NOTES_SOURCE_NAMESPACE

    def execute(
        self,
        request: SearchNotesRequest,
        *,
        trace_id: str | None = None,
    ) -> SearchNotesResponse:
        """执行一次受限检索，并无论成功失败都尝试写入追踪。"""
        normalized_trace_id = _normalize_trace_id(trace_id)
        tool_call_id = str(uuid4())
        started_at = _utc_now()
        started_ns = perf_counter_ns()

        status: SearchNotesStatus
        results: tuple[SearchNotesEvidence, ...] = ()
        error: SearchNotesError | None = None

        validation_error = _validate_request(request)
        if validation_error is not None:
            status = SearchNotesStatus.INVALID_INPUT
            error = validation_error
        else:
            try:
                raw_results = search_chunks(
                    request.query.strip(),
                    top_k=request.top_k,
                    embedding_provider=self.embedding_provider,
                    vector_store=self.vector_store,
                    state_store=self.state_store,
                    source_namespace=self.source_namespace,
                )
                results = _select_evidence(
                    raw_results,
                    min_score=self.min_score,
                    max_total_characters=self.max_total_characters,
                )
                status = (
                    SearchNotesStatus.SUCCESS
                    if results
                    else SearchNotesStatus.NO_RESULTS
                )
            except VectorSearchInputError:
                status = SearchNotesStatus.INVALID_INPUT
                error = SearchNotesError(
                    code="invalid_argument",
                    message="The search request is invalid.",
                    retryable=False,
                )
            except VectorIndexProfileError:
                status = SearchNotesStatus.INDEX_NOT_READY
                error = SearchNotesError(
                    code="index_not_ready",
                    message="The notes index must be built for the current model.",
                    retryable=False,
                )
            except EmbeddingError as caught:
                if _contains_timeout(caught):
                    status = SearchNotesStatus.TIMEOUT
                    error = SearchNotesError(
                        code="embedding_timeout",
                        message="The local embedding operation timed out.",
                        retryable=True,
                    )
                else:
                    status = SearchNotesStatus.EMBEDDING_ERROR
                    error = SearchNotesError(
                        code="embedding_failed",
                        message="The local embedding operation failed.",
                        retryable=True,
                    )
            except (VectorStoreError, sqlite3.DatabaseError):
                status = SearchNotesStatus.STORAGE_ERROR
                error = SearchNotesError(
                    code="retrieval_storage_failed",
                    message="The local retrieval storage is unavailable.",
                    retryable=True,
                )
            except VectorIndexError:
                status = SearchNotesStatus.INTERNAL_ERROR
                error = SearchNotesError(
                    code="retrieval_failed",
                    message="The notes retrieval operation failed.",
                    retryable=True,
                )
            except Exception:
                # 未知异常只能返回稳定类别，不能把正文或第三方细节交给 Agent。
                status = SearchNotesStatus.INTERNAL_ERROR
                error = SearchNotesError(
                    code="internal_error",
                    message="The search tool failed unexpectedly.",
                    retryable=False,
                )

        completed_at = _utc_now()
        duration_ms = max(0, (perf_counter_ns() - started_ns) // 1_000_000)
        response = SearchNotesResponse(
            tool_name=SEARCH_NOTES_TOOL_NAME,
            tool_call_id=tool_call_id,
            trace_id=normalized_trace_id,
            status=status,
            results=results,
            error=error,
            duration_ms=duration_ms,
        )

        trace = ToolTraceRecord(
            tool_call_id=tool_call_id,
            trace_id=normalized_trace_id,
            tool_name=SEARCH_NOTES_TOOL_NAME,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            status=status.value,
            result_count=len(results),
            parameters=_parameter_summary(request, self),
            result_ids=tuple(result.chunk_id for result in results),
            error_code=error.code if error is not None else None,
        )
        try:
            self.trace_store.record(trace)
        except Exception:
            # 追踪是 MVP 的必需能力；写入失败时不把未审计结果伪装成成功。
            return SearchNotesResponse(
                tool_name=SEARCH_NOTES_TOOL_NAME,
                tool_call_id=tool_call_id,
                trace_id=normalized_trace_id,
                status=SearchNotesStatus.INTERNAL_ERROR,
                results=(),
                error=SearchNotesError(
                    code="trace_write_failed",
                    message="The tool result could not be recorded.",
                    retryable=True,
                ),
                duration_ms=duration_ms,
            )
        return response


def _validate_request(request: object) -> SearchNotesError | None:
    """校验输入，但不在错误中回显用户问题。"""
    if not isinstance(request, SearchNotesRequest):
        return SearchNotesError(
            code="invalid_request",
            message="request must be a SearchNotesRequest.",
            retryable=False,
        )
    if not isinstance(request.query, str) or not request.query.strip():
        return SearchNotesError(
            code="invalid_query",
            message="query must be a non-empty string.",
            retryable=False,
        )
    if len(request.query.strip()) > MAX_QUERY_CHARACTERS:
        return SearchNotesError(
            code="query_too_long",
            message=f"query must not exceed {MAX_QUERY_CHARACTERS} characters.",
            retryable=False,
        )
    if (
        isinstance(request.top_k, bool)
        or not isinstance(request.top_k, int)
        or not 1 <= request.top_k <= MAX_TOP_K
    ):
        return SearchNotesError(
            code="invalid_top_k",
            message=f"top_k must be an integer between 1 and {MAX_TOP_K}.",
            retryable=False,
        )
    return None


def _select_evidence(
    results: tuple[ChunkSearchResult, ...],
    *,
    min_score: float,
    max_total_characters: int,
) -> tuple[SearchNotesEvidence, ...]:
    """过滤弱证据、去重并执行正文总字符预算。"""
    selected: list[SearchNotesEvidence] = []
    seen_chunk_ids: set[str] = set()
    remaining_characters = max_total_characters

    for result in results:
        if result.score < min_score or result.chunk_id in seen_chunk_ids:
            continue
        if remaining_characters <= 0:
            break

        content = result.content
        truncated = len(content) > remaining_characters
        if truncated:
            content = content[:remaining_characters]
        if not content:
            continue

        seen_chunk_ids.add(result.chunk_id)
        selected.append(
            SearchNotesEvidence(
                rank=len(selected) + 1,
                chunk_id=result.chunk_id,
                document_id=result.document_id,
                source_type="markdown",
                source_namespace=result.source_namespace,
                relative_path=result.relative_path.as_posix(),
                heading_path=result.heading_path,
                start_line=result.start_line,
                end_line=result.end_line,
                fingerprint=result.fingerprint,
                content=content,
                content_truncated=truncated,
                score=result.score,
            )
        )
        remaining_characters -= len(content)
    return tuple(selected)


def _parameter_summary(
    request: object,
    tool: SearchNotesTool,
) -> tuple[tuple[str, str | int | float | bool], ...]:
    """只记录安全摘要，不记录问题原文或任何笔记正文。"""
    query = getattr(request, "query", None)
    requested_top_k = getattr(request, "top_k", None)
    query_length = len(query.strip()) if isinstance(query, str) else -1
    top_k = (
        requested_top_k
        if isinstance(requested_top_k, int)
        and not isinstance(requested_top_k, bool)
        else -1
    )
    return (
        ("max_total_characters", tool.max_total_characters),
        ("min_score", tool.min_score),
        ("query_length", query_length),
        ("source_namespace", tool.source_namespace),
        ("top_k", top_k),
    )


def _normalize_trace_id(value: str | None) -> str:
    """生成或校验 UUID，防止任意长字符串进入追踪索引。"""
    if value is None:
        return str(uuid4())
    if not isinstance(value, str):
        raise ValueError("trace_id must be a UUID string")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise ValueError("trace_id must be a UUID string") from error


def _contains_timeout(error: BaseException) -> bool:
    """沿异常链查找超时，不暴露具体第三方消息。"""
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, TimeoutError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _utc_now() -> str:
    """返回带时区的 ISO 8601 UTC 时间。"""
    return datetime.now(timezone.utc).isoformat()
