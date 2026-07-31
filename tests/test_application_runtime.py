"""验证真实运行时组装前的数据源隔离和三 namespace 加载。"""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.application.runtime import (
    ApplicationUnavailableError,
    LocalInterviewRuntime,
    _load_index_documents,
    _require_disjoint_source_directories,
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
