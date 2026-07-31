"""实现三个只读语义检索 Tool 共用的命名空间、错误和追踪边界。"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
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

DEFAULT_TOP_K = 5
DEFAULT_MIN_SCORE = 0.58
MAX_TOP_K = 10
MAX_QUERY_CHARACTERS = 480
MAX_TOTAL_CHARACTERS = 20_000

_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ScopedSearchStatus(StrEnum):
    """调用方可稳定判断的只读检索结果状态。"""

    SUCCESS = "success"
    NO_RESULTS = "no_results"
    INVALID_INPUT = "invalid_input"
    INDEX_NOT_READY = "index_not_ready"
    TIMEOUT = "timeout"
    EMBEDDING_ERROR = "embedding_error"
    STORAGE_ERROR = "storage_error"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class ScopedSearchRequest:
    """三个语义检索 Tool 共用的问题和 Top-K 输入。"""

    query: str
    top_k: int = DEFAULT_TOP_K


@dataclass(frozen=True, slots=True)
class ScopedSearchEvidence:
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
class ScopedSearchError:
    """不会暴露第三方异常和私人正文的稳定错误结构。"""

    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class ScopedSearchResponse:
    """只读语义检索 Tool 的稳定输出结构。"""

    tool_name: str
    tool_call_id: str
    trace_id: str
    status: ScopedSearchStatus
    results: tuple[ScopedSearchEvidence, ...]
    error: ScopedSearchError | None
    duration_ms: int


@dataclass(frozen=True, slots=True)
class ScopedSearchPolicy:
    """把每个 Tool 的名称、数据边界和正文处理固定在构造阶段。"""

    tool_name: str
    source_namespace: str
    content_transform: Callable[[str], str] | None = None

    def __post_init__(self) -> None:
        if not _SAFE_IDENTIFIER_PATTERN.fullmatch(self.tool_name):
            raise ValueError("tool_name must be a safe lowercase identifier")
        if not _SAFE_IDENTIFIER_PATTERN.fullmatch(self.source_namespace):
            raise ValueError(
                "source_namespace must be a safe lowercase identifier"
            )
        if self.content_transform is not None and not callable(
            self.content_transform
        ):
            raise ValueError("content_transform must be callable or None")


class ScopedSemanticSearchTool:
    """检索构造时固定的命名空间，不接受调用方传入路径或数据源。"""

    def __init__(
        self,
        *,
        policy: ScopedSearchPolicy,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        state_store: VectorIndexStateStore,
        trace_store: ToolTraceStore,
        min_score: float = DEFAULT_MIN_SCORE,
        max_total_characters: int = 6000,
    ) -> None:
        if not isinstance(policy, ScopedSearchPolicy):
            raise ValueError("policy must be a ScopedSearchPolicy")
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

        self.policy = policy
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.state_store = state_store
        self.trace_store = trace_store
        self.min_score = float(min_score)
        self.max_total_characters = max_total_characters
        self.tool_name = policy.tool_name
        self.source_namespace = policy.source_namespace

    def execute(
        self,
        request: ScopedSearchRequest,
        *,
        trace_id: str | None = None,
    ) -> ScopedSearchResponse:
        """执行一次受限检索，并无论成功失败都尝试写入追踪。"""
        normalized_trace_id = _normalize_trace_id(trace_id)
        tool_call_id = str(uuid4())
        started_at = _utc_now()
        started_ns = perf_counter_ns()

        status: ScopedSearchStatus
        results: tuple[ScopedSearchEvidence, ...] = ()
        error: ScopedSearchError | None = None

        validation_error = _validate_request(request)
        if validation_error is not None:
            status = ScopedSearchStatus.INVALID_INPUT
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
                    content_transform=self.policy.content_transform,
                )
                status = (
                    ScopedSearchStatus.SUCCESS
                    if results
                    else ScopedSearchStatus.NO_RESULTS
                )
            except VectorSearchInputError:
                status = ScopedSearchStatus.INVALID_INPUT
                error = ScopedSearchError(
                    code="invalid_argument",
                    message="The search request is invalid.",
                    retryable=False,
                )
            except VectorIndexProfileError:
                status = ScopedSearchStatus.INDEX_NOT_READY
                error = ScopedSearchError(
                    code="index_not_ready",
                    message="The local index must be built for the current model.",
                    retryable=False,
                )
            except EmbeddingError as caught:
                if _contains_timeout(caught):
                    status = ScopedSearchStatus.TIMEOUT
                    error = ScopedSearchError(
                        code="embedding_timeout",
                        message="The local embedding operation timed out.",
                        retryable=True,
                    )
                else:
                    status = ScopedSearchStatus.EMBEDDING_ERROR
                    error = ScopedSearchError(
                        code="embedding_failed",
                        message="The local embedding operation failed.",
                        retryable=True,
                    )
            except (VectorStoreError, sqlite3.DatabaseError):
                status = ScopedSearchStatus.STORAGE_ERROR
                error = ScopedSearchError(
                    code="retrieval_storage_failed",
                    message="The local retrieval storage is unavailable.",
                    retryable=True,
                )
            except VectorIndexError:
                status = ScopedSearchStatus.INTERNAL_ERROR
                error = ScopedSearchError(
                    code="retrieval_failed",
                    message="The local retrieval operation failed.",
                    retryable=True,
                )
            except Exception:
                # 未知异常只能返回稳定类别，不能把正文或第三方细节交给 Agent。
                status = ScopedSearchStatus.INTERNAL_ERROR
                error = ScopedSearchError(
                    code="internal_error",
                    message="The search Tool failed unexpectedly.",
                    retryable=False,
                )

        completed_at = _utc_now()
        duration_ms = max(0, (perf_counter_ns() - started_ns) // 1_000_000)
        response = ScopedSearchResponse(
            tool_name=self.tool_name,
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
            tool_name=self.tool_name,
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
            return ScopedSearchResponse(
                tool_name=self.tool_name,
                tool_call_id=tool_call_id,
                trace_id=normalized_trace_id,
                status=ScopedSearchStatus.INTERNAL_ERROR,
                results=(),
                error=ScopedSearchError(
                    code="trace_write_failed",
                    message="The Tool result could not be recorded.",
                    retryable=True,
                ),
                duration_ms=duration_ms,
            )
        return response


def _validate_request(request: object) -> ScopedSearchError | None:
    """校验输入，但不在错误中回显用户问题。"""
    if not isinstance(request, ScopedSearchRequest):
        return ScopedSearchError(
            code="invalid_request",
            message="request must be a scoped search request.",
            retryable=False,
        )
    if not isinstance(request.query, str) or not request.query.strip():
        return ScopedSearchError(
            code="invalid_query",
            message="query must be a non-empty string.",
            retryable=False,
        )
    if len(request.query.strip()) > MAX_QUERY_CHARACTERS:
        return ScopedSearchError(
            code="query_too_long",
            message=f"query must not exceed {MAX_QUERY_CHARACTERS} characters.",
            retryable=False,
        )
    if (
        isinstance(request.top_k, bool)
        or not isinstance(request.top_k, int)
        or not 1 <= request.top_k <= MAX_TOP_K
    ):
        return ScopedSearchError(
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
    content_transform: Callable[[str], str] | None,
) -> tuple[ScopedSearchEvidence, ...]:
    """过滤弱证据、去重、转换敏感正文并执行总字符预算。"""
    selected: list[ScopedSearchEvidence] = []
    seen_chunk_ids: set[str] = set()
    remaining_characters = max_total_characters

    for result in results:
        if result.score < min_score or result.chunk_id in seen_chunk_ids:
            continue
        if remaining_characters <= 0:
            break

        content = result.content
        if content_transform is not None:
            content = content_transform(content)
        if not isinstance(content, str):
            raise TypeError("content_transform must return a string")
        truncated = len(content) > remaining_characters
        if truncated:
            content = content[:remaining_characters]
        if not content:
            continue

        seen_chunk_ids.add(result.chunk_id)
        selected.append(
            ScopedSearchEvidence(
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
    tool: ScopedSemanticSearchTool,
) -> tuple[tuple[str, str | int | float | bool], ...]:
    """只记录安全摘要，不记录问题或任何资料正文。"""
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
