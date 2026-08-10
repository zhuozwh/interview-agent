"""验证真实运行时组装前的数据源隔离和三 namespace 加载。"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import sleep

import pytest

import interview_agent.application.runtime as runtime_module
from interview_agent.agent import AgentRequest, AgentStatus
from interview_agent.application.runtime import (
    ApplicationUnavailableError,
    LocalInterviewRuntime,
    build_local_runtime,
    _load_index_documents,
    _require_disjoint_source_directories,
)
from interview_agent.core.config import Settings
from interview_agent.llm import LLMResponse, LLMUsage
from interview_agent.storage import (
    SQLiteAgentTraceStore,
    SQLiteDatabase,
    SQLiteToolTraceStore,
)


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """三个 Markdown 数据源使用自动清理目录。"""
    with TemporaryDirectory(prefix="interview-agent-runtime-test-") as directory:
        yield Path(directory)


def _create_sources(root: Path) -> tuple[Path, Path, Path]:
    """创建同一允许目录下互不包含的三个数据源。"""
    notes = root / "interview"
    projects = root / "projects"
    resume = root / "resume"
    notes.mkdir(parents=True)
    projects.mkdir()
    resume.mkdir()
    return notes, projects, resume


def test_runtime_rejects_overlapping_real_source_directories(
    temporary_directory: Path,
) -> None:
    """notes 不能成为 projects/resume 的父目录，否则会重复或越界索引。"""
    allowed = temporary_directory / "knowledge"
    notes = allowed
    projects = allowed / "projects"
    resume = temporary_directory / "resume"
    projects.mkdir(parents=True)
    resume.mkdir()

    with pytest.raises(ApplicationUnavailableError, match="must not overlap"):
        _require_disjoint_source_directories((notes, projects, resume))


def test_runtime_loads_three_namespaces_with_stable_source_boundaries(
    temporary_directory: Path,
) -> None:
    """同一索引计划包含三类资料，但每个文档保留固定 namespace。"""
    allowed = temporary_directory / "knowledge"
    notes, projects, resume = _create_sources(allowed)
    (notes / "smart-pointer.md").write_text(
        "# 智能指针\nRAII 管理资源。",
        encoding="utf-8",
    )
    (projects / "server.md").write_text(
        "# 服务框架\n当前实现单 Reactor。",
        encoding="utf-8",
    )
    (resume / "resume.md").write_text(
        "# 后端经历\n实习期间实现服务模块。",
        encoding="utf-8",
    )
    sources = _require_disjoint_source_directories(
        (notes, projects, resume)
    )

    documents = _load_index_documents(
        sources,
        allowed_directories=(allowed,),
        max_file_size_bytes=1024,
        max_total_size_bytes=4096,
        max_chunk_characters=500,
    )

    assert [
        (document.source_namespace, document.relative_path.as_posix())
        for document in documents
    ] == [
        ("notes", "smart-pointer.md"),
        ("projects", "server.md"),
        ("resume", "resume.md"),
    ]
    assert len({document.document_id for document in documents}) == 3


def test_runtime_enforces_total_bytes_across_all_sources(
    temporary_directory: Path,
) -> None:
    """单个目录未超限也不能让三个目录合计绕过总读取预算。"""
    allowed = temporary_directory / "knowledge"
    notes, projects, resume = _create_sources(allowed)
    for source, name in (
        (notes, "notes.md"),
        (projects, "project.md"),
        (resume, "resume.md"),
    ):
        (source / name).write_text("1234567890", encoding="utf-8")

    with pytest.raises(ApplicationUnavailableError, match="Combined"):
        _load_index_documents(
            (notes, projects, resume),
            allowed_directories=(allowed,),
            max_file_size_bytes=20,
            max_total_size_bytes=20,
            max_chunk_characters=500,
        )


@pytest.mark.parametrize(
    ("field_name", "target_source", "target_name"),
    [
        ("database_path", "notes", "runtime.sqlite3"),
        ("vector_store_path", "projects", "vectors"),
        ("embedding_cache_directory", "resume", "models"),
    ],
)
def test_runtime_rejects_writable_storage_inside_read_only_sources(
    temporary_directory: Path,
    field_name: str,
    target_source: str,
    target_name: str,
) -> None:
    """配置失误不能让 SQLite、Chroma 或模型缓存写进 Markdown 数据源。"""
    allowed = temporary_directory / "knowledge"
    notes, projects, resume = _create_sources(allowed)
    sources = {
        "notes": notes,
        "projects": projects,
        "resume": resume,
    }
    target_path = sources[target_source] / target_name
    settings_values = {
        "markdown_source_directory": notes,
        "project_source_directory": projects,
        "resume_source_directory": resume,
        "allowed_data_directories": (allowed,),
        "database_path": temporary_directory / "runtime.sqlite3",
        "vector_store_path": temporary_directory / "vectors",
        "embedding_cache_directory": temporary_directory / "models",
        "llm_api_key": "test-only-key",
        field_name: target_path,
        "_env_file": None,
    }

    with pytest.raises(ApplicationUnavailableError, match="must stay outside"):
        build_local_runtime(Settings(**settings_values))

    assert not target_path.exists()


@pytest.mark.parametrize(
    "field_name",
    ["vector_store_path", "embedding_cache_directory"],
)
def test_runtime_rejects_writable_directory_that_contains_sources(
    temporary_directory: Path,
    field_name: str,
) -> None:
    """目录型运行时根不能反向包住数据源，避免清理运行时时误删原文。"""
    allowed = temporary_directory / "knowledge"
    notes, projects, resume = _create_sources(allowed)
    settings_values = {
        "markdown_source_directory": notes,
        "project_source_directory": projects,
        "resume_source_directory": resume,
        "allowed_data_directories": (allowed,),
        "database_path": temporary_directory / "runtime.sqlite3",
        "vector_store_path": temporary_directory / "vectors",
        "embedding_cache_directory": temporary_directory / "models",
        "llm_api_key": "test-only-key",
        field_name: allowed,
        "_env_file": None,
    }

    with pytest.raises(ApplicationUnavailableError, match="must stay outside"):
        build_local_runtime(Settings(**settings_values))


def test_runtime_close_releases_llm_even_if_vector_close_fails() -> None:
    """一个资源关闭异常不能阻止另一个外部连接池释放。"""

    class FailingVectorStore:
        def close(self):
            raise RuntimeError("vector close failed")

    class ClosingLLM:
        def __init__(self) -> None:
            self.closed = False

        def close(self):
            self.closed = True

    llm = ClosingLLM()
    runtime = LocalInterviewRuntime(
        use_case=object(),
        vector_store=FailingVectorStore(),
        llm_client=llm,
        sync_report=object(),
    )
    with pytest.raises(RuntimeError, match="vector close failed"):
        runtime.close()
    assert llm.closed is True


def test_build_local_runtime_wires_configured_three_source_workflow(
    temporary_directory: Path,
    monkeypatch,
) -> None:
    """真实组装函数连接 Chroma/SQLite，模型和远端调用使用确定性替身。"""

    class RuntimeEmbedding:
        model_name = "runtime-composition-embedding-v1"
        dimension = 3

        def __init__(self, **kwargs) -> None:
            self.configuration = kwargs

        def embed_texts(self, texts):
            return [self._vector(text) for text in texts]

        def embed_query(self, query):
            return self._vector(query)

        @staticmethod
        def _vector(text):
            if "事件循环" in text or "Reactor" in text:
                return [1.0, 0.01, 0.01]
            if "实习" in text:
                return [0.01, 1.0, 0.01]
            return [0.01, 0.01, 1.0]

    class RuntimeLLM:
        def __init__(self, **kwargs) -> None:
            self.configuration = kwargs
            self.closed = False

        def complete(self, messages):
            return LLMResponse(
                request_id="runtime-request-1",
                model="test-model",
                content="当前项目实现了单 Reactor。[S1]",
                finish_reason="stop",
                system_fingerprint=None,
                usage=LLMUsage(
                    prompt_tokens=10,
                    completion_tokens=8,
                    total_tokens=18,
                ),
            )

        def close(self):
            self.closed = True

    monkeypatch.setattr(
        runtime_module,
        "FastEmbedEmbeddingProvider",
        RuntimeEmbedding,
    )
    monkeypatch.setattr(
        runtime_module,
        "OpenAICompatibleLLMClient",
        RuntimeLLM,
    )

    allowed = temporary_directory / "knowledge"
    notes, projects, resume = _create_sources(allowed)
    (notes / "notes.md").write_text(
        "# 智能指针\nRAII 管理资源。",
        encoding="utf-8",
    )
    (projects / "server.md").write_text(
        "# 事件循环\n当前项目实现单 Reactor。",
        encoding="utf-8",
    )
    (resume / "resume.md").write_text(
        "# 后端经历\n实习期间实现服务模块。",
        encoding="utf-8",
    )
    database_path = temporary_directory / "runtime.sqlite3"
    settings = Settings(
        markdown_source_directory=notes,
        project_source_directory=projects,
        resume_source_directory=resume,
        allowed_data_directories=(allowed,),
        database_path=database_path,
        vector_store_path=temporary_directory / "vectors",
        embedding_cache_directory=temporary_directory / "models",
        llm_api_key="test-only-key",
        _env_file=None,
    )

    runtime = build_local_runtime(settings)
    result = runtime.execute(
        AgentRequest(question="我的项目事件循环实现状态是什么？")
    )
    assert runtime.sync_report.embedded_document_count == 3
    assert result.response.status is AgentStatus.SUCCESS
    assert result.response.citations[0].source_namespace == "projects"
    assert runtime.llm_client.configuration["api_key"] == "test-only-key"

    database = SQLiteDatabase(database_path)
    assert len(SQLiteAgentTraceStore(database).load_records()) == 1
    assert [
        trace.tool_name
        for trace in SQLiteToolTraceStore(database).load_records()
    ] == ["get_project_context"]
    llm = runtime.llm_client
    runtime.close()
    assert llm.closed is True


def test_local_runtime_serializes_shared_single_user_resources() -> None:
    """并发 HTTP 工作线程不能同时进入共享模型、Chroma 和 SQLite 用例。"""

    class DetectingUseCase:
        def __init__(self) -> None:
            self.lock = Lock()
            self.active = 0
            self.max_active = 0

        def execute(self, request, *, session_id=None):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            sleep(0.02)
            with self.lock:
                self.active -= 1
            return request.question

    class Closable:
        def close(self):
            return None

    use_case = DetectingUseCase()
    runtime = LocalInterviewRuntime(
        use_case=use_case,
        vector_store=Closable(),
        llm_client=Closable(),
        sync_report=object(),
    )
    requests = tuple(
        AgentRequest(question=f"question-{index}") for index in range(4)
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(runtime.execute, requests))

    assert results == tuple(request.question for request in requests)
    assert use_case.max_active == 1
    runtime.close()
