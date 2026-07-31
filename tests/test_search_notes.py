"""验证 search_notes 的证据边界、错误映射、字符预算和追踪。"""

import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.retrieval import (
    build_index_plan,
    load_markdown_documents,
    prepare_index_documents,
    synchronize_vector_index,
)
from interview_agent.storage import (
    ChromaVectorStore,
    SQLiteDatabase,
    SQLiteIndexStateStore,
    SQLiteToolTraceStore,
)
from interview_agent.tools import (
    SearchNotesRequest,
    SearchNotesStatus,
    SearchNotesTool,
)


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """所有 Markdown、SQLite 和 Chroma 测试数据都自动清理。"""
    with TemporaryDirectory(prefix="interview-agent-search-notes-test-") as directory:
        yield Path(directory)


class ToolTestEmbedding:
    """使用三维确定向量隔离 Tool 测试与真实模型下载。"""

    model_name = "tool-test-embedding-v1"
    dimension = 3

    def __init__(self) -> None:
        self.query_calls: list[str] = []

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        self.query_calls.append(query)
        return self._vector(query)

    @staticmethod
    def _vector(text: str) -> list[float]:
        if "智能指针" in text or "内存" in text:
            return [1.0, 0.05, 0.05]
        if "网络" in text or "事件循环" in text:
            return [0.05, 1.0, 0.05]
        return [0.05, 0.05, 1.0]


class TimeoutQueryEmbedding(ToolTestEmbedding):
    """模型身份不变，只在查询阶段模拟超时。"""

    def embed_query(self, query: str) -> list[float]:
        raise TimeoutError("simulated local timeout")


class FailingTraceStore:
    """模拟审计写入失败。"""

    def record(self, trace) -> None:
        raise sqlite3.DatabaseError("simulated trace failure")


class FailingStateStore:
    """模拟 SQLite 检索状态不可用。"""

    def load_vector_profile(self):
        raise sqlite3.DatabaseError("simulated state failure")

    def apply_plan(self, plan, *, vector_profile=None) -> None:
        raise AssertionError("apply_plan must not be called while searching")


class UnusedVectorStore:
    """状态读取先失败时，向量存储不应被调用。"""

    def __getattr__(self, name):
        raise AssertionError(f"vector store method must not be called: {name}")


def _prepare(source_directory: Path):
    """执行真实 Markdown 读取、切分和索引记录准备。"""
    return prepare_index_documents(
        load_markdown_documents(source_directory, [source_directory.parent]),
        max_chunk_characters=1000,
        source_namespace="notes",
    )


def _create_initialized_stores(temporary_directory: Path):
    """创建共享同一 SQLite 文件的索引状态和 Tool 追踪仓储。"""
    database = SQLiteDatabase(temporary_directory / "state.db")
    state_store = SQLiteIndexStateStore(database)
    trace_store = SQLiteToolTraceStore(database)
    state_store.initialize()
    trace_store.initialize()
    return state_store, trace_store


def test_search_notes_returns_bounded_evidence_and_persists_trace(
    temporary_directory: Path,
) -> None:
    source = temporary_directory / "allowed" / "notes"
    source.mkdir(parents=True)
    (source / "memory.md").write_text(
        "# C++ 内存\n智能指针通过引用计数管理对象生命周期。",
        encoding="utf-8",
    )
    (source / "network.md").write_text(
        "# 网络\n事件循环负责处理网络连接。",
        encoding="utf-8",
    )
    state_store, trace_store = _create_initialized_stores(temporary_directory)
    provider = ToolTestEmbedding()
    trace_id = "11111111-1111-4111-8111-111111111111"

    with ChromaVectorStore(temporary_directory / "vectors") as vector_store:
        current = _prepare(source)
        synchronize_vector_index(
            build_index_plan(current, ()),
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
        )
        tool = SearchNotesTool(
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
            min_score=0.5,
            max_total_characters=6000,
        )

        response = tool.execute(
            SearchNotesRequest(query="智能指针如何管理内存？", top_k=2),
            trace_id=trace_id,
        )

        assert response.status is SearchNotesStatus.SUCCESS
        assert response.trace_id == trace_id
        assert response.error is None
        assert response.results[0].relative_path == "memory.md"
        assert response.results[0].heading_path == ("C++ 内存",)
        assert response.results[0].source_type == "markdown"
        assert response.results[0].source_namespace == "notes"
        assert response.results[0].start_line == 1
        assert response.results[0].end_line == 2
        assert response.results[0].score > 0.9
        assert response.results[0].content_truncated is False
        assert all(not Path(item.relative_path).is_absolute() for item in response.results)

    traces = trace_store.load_records(trace_id)
    assert len(traces) == 1
    assert traces[0].status == "success"
    assert traces[0].result_ids == tuple(
        result.chunk_id for result in response.results
    )
    assert dict(traces[0].parameters)["query_length"] == len(
        "智能指针如何管理内存？"
    )
    assert "智能指针" not in str(traces[0].parameters)


def test_weak_results_become_explicit_no_results_and_content_is_bounded(
    temporary_directory: Path,
) -> None:
    source = temporary_directory / "allowed" / "notes"
    source.mkdir(parents=True)
    (source / "memory.md").write_text(
        "# 内存\n智能指针内容足够长，用于验证 Tool 的正文字符预算。",
        encoding="utf-8",
    )
    state_store, trace_store = _create_initialized_stores(temporary_directory)
    provider = ToolTestEmbedding()

    with ChromaVectorStore(temporary_directory / "vectors") as vector_store:
        current = _prepare(source)
        synchronize_vector_index(
            build_index_plan(current, ()),
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
        )
        tool = SearchNotesTool(
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
            min_score=0.5,
            max_total_characters=10,
        )

        unrelated = tool.execute(SearchNotesRequest(query="完全无关的问题"))
        assert unrelated.status is SearchNotesStatus.NO_RESULTS
        assert unrelated.results == ()
        assert unrelated.error is None

        bounded = tool.execute(SearchNotesRequest(query="智能指针"))
        assert bounded.status is SearchNotesStatus.SUCCESS
        assert sum(len(item.content) for item in bounded.results) <= 10
        assert bounded.results[0].content_truncated is True


@pytest.mark.parametrize(
    ("case_request", "error_code"),
    [
        (None, "invalid_request"),
        (SearchNotesRequest(query=" "), "invalid_query"),
        (SearchNotesRequest(query="问题", top_k=0), "invalid_top_k"),
        (SearchNotesRequest(query="问题", top_k=11), "invalid_top_k"),
        (SearchNotesRequest(query="问" * 481), "query_too_long"),
    ],
)
def test_invalid_input_returns_stable_error_and_is_traced(
    temporary_directory: Path,
    case_request,
    error_code: str,
) -> None:
    state_store, trace_store = _create_initialized_stores(temporary_directory)

    with ChromaVectorStore(temporary_directory / "vectors") as vector_store:
        tool = SearchNotesTool(
            embedding_provider=ToolTestEmbedding(),
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
        )
        response = tool.execute(case_request)

    assert response.status is SearchNotesStatus.INVALID_INPUT
    assert response.error.code == error_code
    trace = trace_store.load_records(response.trace_id)[0]
    assert trace.status == "invalid_input"
    assert trace.error_code == error_code


def test_index_not_ready_and_embedding_timeout_are_mapped(
    temporary_directory: Path,
) -> None:
    source = temporary_directory / "allowed" / "notes"
    source.mkdir(parents=True)
    (source / "note.md").write_text("# 内存\n智能指针", encoding="utf-8")
    state_store, trace_store = _create_initialized_stores(temporary_directory)
    provider = ToolTestEmbedding()

    with ChromaVectorStore(temporary_directory / "vectors") as vector_store:
        not_ready_tool = SearchNotesTool(
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
        )
        not_ready = not_ready_tool.execute(SearchNotesRequest(query="智能指针"))
        assert not_ready.status is SearchNotesStatus.INDEX_NOT_READY
        assert not_ready.error.code == "index_not_ready"

        current = _prepare(source)
        synchronize_vector_index(
            build_index_plan(current, ()),
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
        )
        timeout_tool = SearchNotesTool(
            embedding_provider=TimeoutQueryEmbedding(),
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
        )
        timeout = timeout_tool.execute(SearchNotesRequest(query="智能指针"))

        assert timeout.status is SearchNotesStatus.TIMEOUT
        assert timeout.error.code == "embedding_timeout"
        assert timeout.error.retryable is True


def test_storage_and_trace_failures_do_not_expose_partial_success(
    temporary_directory: Path,
) -> None:
    _, trace_store = _create_initialized_stores(temporary_directory)
    storage_tool = SearchNotesTool(
        embedding_provider=ToolTestEmbedding(),
        vector_store=UnusedVectorStore(),
        state_store=FailingStateStore(),
        trace_store=trace_store,
    )

    storage_failure = storage_tool.execute(SearchNotesRequest(query="问题"))
    assert storage_failure.status is SearchNotesStatus.STORAGE_ERROR
    assert storage_failure.results == ()
    assert storage_failure.error.code == "retrieval_storage_failed"

    with ChromaVectorStore(temporary_directory / "vectors") as vector_store:
        trace_failure_tool = SearchNotesTool(
            embedding_provider=ToolTestEmbedding(),
            vector_store=vector_store,
            state_store=FailingStateStore(),
            trace_store=FailingTraceStore(),
        )
        trace_failure = trace_failure_tool.execute(SearchNotesRequest(query=" "))

    assert trace_failure.status is SearchNotesStatus.INTERNAL_ERROR
    assert trace_failure.results == ()
    assert trace_failure.error.code == "trace_write_failed"
