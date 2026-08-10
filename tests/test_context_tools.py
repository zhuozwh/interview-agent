"""验证三个只读 Tool 的命名空间隔离、简历脱敏和统一追踪。"""

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
    GetProjectContextRequest,
    GetProjectContextStatus,
    GetProjectContextTool,
    GetResumeContextRequest,
    GetResumeContextStatus,
    GetResumeContextTool,
    SearchNotesRequest,
    SearchNotesStatus,
    SearchNotesTool,
)
from interview_agent.tools.scoped_search import (
    ScopedSearchPolicy,
    ScopedSearchRequest,
    ScopedSearchStatus,
    ScopedSemanticSearchTool,
)

_TRACE_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """Markdown、SQLite 和 Chroma 测试数据使用自动清理目录。"""
    with TemporaryDirectory(prefix="interview-agent-context-tools-test-") as directory:
        yield Path(directory)


class ScopedToolEmbedding:
    """按主题生成确定向量，验证 namespace 过滤而不下载模型。"""

    model_name = "scoped-tool-test-embedding-v1"
    dimension = 4

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self._vector(query)

    @staticmethod
    def _vector(text: str) -> list[float]:
        if "智能指针" in text or "RAII" in text:
            return [1.0, 0.01, 0.01, 0.01]
        if "事件循环" in text or "服务框架" in text:
            return [0.01, 1.0, 0.01, 0.01]
        if "实习" in text or "后端经历" in text:
            return [0.01, 0.01, 1.0, 0.01]
        return [0.01, 0.01, 0.01, 1.0]


def _prepare_namespace(source: Path, namespace: str):
    """使用真实加载、切分和指纹逻辑准备一个独立数据源。"""
    return prepare_index_documents(
        load_markdown_documents(source, (source.parent,)),
        max_chunk_characters=500,
        source_namespace=namespace,
    )


def _create_stores(temporary_directory: Path):
    """三个 Tool 共用同一 SQLite 状态、追踪仓储和向量集合。"""
    database = SQLiteDatabase(temporary_directory / "state.sqlite3")
    state_store = SQLiteIndexStateStore(database)
    trace_store = SQLiteToolTraceStore(database)
    state_store.initialize()
    trace_store.initialize()
    return state_store, trace_store


def test_three_tools_only_return_their_configured_namespaces(
    temporary_directory: Path,
) -> None:
    """调用方不能通过查询内容让一个 Tool 越权读取另一类资料。"""
    allowed = temporary_directory / "allowed"
    notes = allowed / "notes"
    projects = allowed / "projects"
    resume = allowed / "resume"
    notes.mkdir(parents=True)
    projects.mkdir()
    resume.mkdir()
    (notes / "memory.md").write_text(
        "# 智能指针\nRAII 使用对象生命周期管理资源。",
        encoding="utf-8",
    )
    (projects / "server.md").write_text(
        "# C++ 服务框架\n事件循环负责连接调度；当前只实现单 Reactor。",
        encoding="utf-8",
    )
    (resume / "resume.md").write_text(
        "# 后端经历\n实习期间实现 C++ 服务模块。\n"
        "邮箱：candidate@example.com\n手机：13812345678",
        encoding="utf-8",
    )

    documents = (
        *_prepare_namespace(notes, "notes"),
        *_prepare_namespace(projects, "projects"),
        *_prepare_namespace(resume, "resume"),
    )
    state_store, trace_store = _create_stores(temporary_directory)
    provider = ScopedToolEmbedding()

    with ChromaVectorStore(temporary_directory / "chroma") as vector_store:
        synchronize_vector_index(
            build_index_plan(documents, ()),
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
        )
        search_notes = SearchNotesTool(
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
            min_score=0.5,
        )
        project_tool = GetProjectContextTool(
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
            min_score=0.5,
        )
        resume_tool = GetResumeContextTool(
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
            min_score=0.5,
        )

        notes_response = search_notes.execute(
            SearchNotesRequest(query="智能指针如何体现 RAII？"),
            trace_id=_TRACE_ID,
        )
        project_response = project_tool.execute(
            GetProjectContextRequest(query="服务框架的事件循环实现到哪里？"),
            trace_id=_TRACE_ID,
        )
        resume_response = resume_tool.execute(
            GetResumeContextRequest(query="我的 C++ 后端实习经历是什么？"),
            trace_id=_TRACE_ID,
        )

    assert notes_response.status is SearchNotesStatus.SUCCESS
    assert {item.source_namespace for item in notes_response.results} == {"notes"}
    assert {item.relative_path for item in notes_response.results} == {"memory.md"}

    assert project_response.status is GetProjectContextStatus.SUCCESS
    assert project_response.tool_name == "get_project_context"
    assert {item.source_namespace for item in project_response.results} == {
        "projects"
    }
    assert {item.relative_path for item in project_response.results} == {
        "server.md"
    }

    assert resume_response.status is GetResumeContextStatus.SUCCESS
    assert resume_response.tool_name == "get_resume_context"
    assert {item.source_namespace for item in resume_response.results} == {
        "resume"
    }
    resume_content = "\n".join(item.content for item in resume_response.results)
    assert "candidate@example.com" not in resume_content
    assert "13812345678" not in resume_content
    assert "[REDACTED_EMAIL]" in resume_content
    assert "[REDACTED_PHONE]" in resume_content

    traces = trace_store.load_records(_TRACE_ID)
    assert [trace.tool_name for trace in traces] == [
        "search_notes",
        "get_project_context",
        "get_resume_context",
    ]
    assert all(
        "query" not in dict(trace.parameters)
        and "智能指针" not in str(trace.parameters)
        and "实习" not in str(trace.parameters)
        for trace in traces
    )


def test_resume_tool_redacts_formatted_contacts_and_identity_number(
    temporary_directory: Path,
) -> None:
    """常见联系方式和身份证号不能进入 Agent 上下文或 Tool 追踪。"""
    allowed = temporary_directory / "allowed"
    resume = allowed / "resume"
    resume.mkdir(parents=True)
    sensitive_values = (
        "person.name+job@example.com",
        "+86 139-1234-5678",
        "11010519491231002X",
        "private_wechat_123",
        "file:///E:/private/resume/candidate.docx",
        "/home/candidate/private/resume.md",
    )
    (resume / "resume.md").write_text(
        "# 后端经历\n"
        "实习期间参与后端服务开发，2024 年完成性能优化。\n"
        f"邮箱：{sensitive_values[0]}\n"
        f"手机：{sensitive_values[1]}\n"
        f"身份证：{sensitive_values[2]}\n"
        f"WeChat: {sensitive_values[3]}\n"
        f"原件：{sensitive_values[4]}\n"
        f"快照：{sensitive_values[5]}",
        encoding="utf-8",
    )
    state_store, trace_store = _create_stores(temporary_directory)
    provider = ScopedToolEmbedding()
    documents = _prepare_namespace(resume, "resume")

    with ChromaVectorStore(temporary_directory / "chroma") as vector_store:
        synchronize_vector_index(
            build_index_plan(documents, ()),
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
        )
        response = GetResumeContextTool(
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
            min_score=0.5,
        ).execute(
            GetResumeContextRequest(query="后端实习经历"),
            trace_id=_TRACE_ID,
        )

    assert response.status is GetResumeContextStatus.SUCCESS
    content = "\n".join(item.content for item in response.results)
    assert all(value not in content for value in sensitive_values)
    assert "[REDACTED_EMAIL]" in content
    assert "[REDACTED_PHONE]" in content
    assert "[REDACTED_ID]" in content
    assert "[REDACTED_ACCOUNT]" in content
    assert "[REDACTED_LOCAL_PATH]" in content
    assert "2024" in content
    assert all(
        value not in str(trace_store.load_records(_TRACE_ID))
        for value in sensitive_values
    )


def test_scoped_policy_and_transform_failures_are_explicit(
    temporary_directory: Path,
) -> None:
    """非法命名空间在构造时失败，正文转换异常则返回可追踪内部错误。"""
    with pytest.raises(ValueError, match="tool_name"):
        ScopedSearchPolicy(
            tool_name="Bad Tool",
            source_namespace="resume",
        )
    with pytest.raises(ValueError, match="source_namespace"):
        ScopedSearchPolicy(
            tool_name="safe_tool",
            source_namespace="../resume",
        )

    state_store, trace_store = _create_stores(temporary_directory)
    provider = ScopedToolEmbedding()
    source = temporary_directory / "allowed" / "resume"
    source.mkdir(parents=True)
    (source / "resume.md").write_text(
        "# 实习\n后端实习经历",
        encoding="utf-8",
    )
    documents = _prepare_namespace(source, "resume")

    def invalid_transform(content: str):
        return None

    with ChromaVectorStore(temporary_directory / "chroma") as vector_store:
        synchronize_vector_index(
            build_index_plan(documents, ()),
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
        )
        tool = ScopedSemanticSearchTool(
            policy=ScopedSearchPolicy(
                tool_name="test_resume",
                source_namespace="resume",
                content_transform=invalid_transform,
            ),
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
            min_score=0.5,
        )
        response = tool.execute(
            ScopedSearchRequest(query="实习"),
            trace_id=_TRACE_ID,
        )

    assert response.status is ScopedSearchStatus.INTERNAL_ERROR
    assert response.results == ()
    assert response.error.code == "internal_error"
    trace = trace_store.load_records(_TRACE_ID)[0]
    assert trace.status == "internal_error"
    assert trace.error_code == "internal_error"
