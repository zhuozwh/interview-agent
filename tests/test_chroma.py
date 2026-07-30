"""验证 Chroma 的持久化、检索、元数据过滤、删除和显式关闭。"""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.retrieval import VectorIndexProfile, VectorRecord
from interview_agent.storage import ChromaVectorStore, ChromaVectorStoreError


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """Chroma 文件全部位于退出时自动清理的临时目录。"""
    with TemporaryDirectory(prefix="interview-agent-chroma-test-") as directory:
        yield Path(directory)


def _profile(
    *,
    model_name: str = "test-embedding-v1",
    dimension: int = 3,
) -> VectorIndexProfile:
    """构造不会访问真实供应方的测试配置。"""
    return VectorIndexProfile(
        embedding_model=model_name,
        embedding_dimension=dimension,
    )


def _record(
    *,
    chunk_id: str,
    document_id: str,
    namespace: str,
    path: str,
    content: str,
    embedding: tuple[float, ...],
) -> VectorRecord:
    """构造包含完整引用信息的最小向量记录。"""
    return VectorRecord(
        chunk_id=chunk_id,
        document_id=document_id,
        source_namespace=namespace,
        relative_path=Path(path),
        chunk_index=0,
        heading_path=("一级标题", "二级标题"),
        start_line=3,
        end_line=5,
        fingerprint="a" * 64,
        content=content,
        embedding=embedding,
    )


def test_upserts_searches_filters_deletes_and_recovers_after_restart(
    temporary_directory: Path,
) -> None:
    path = temporary_directory / "vectors"
    profile = _profile()
    records = (
        _record(
            chunk_id="chunk-smart-pointer",
            document_id="doc-cpp",
            namespace="notes",
            path="cpp/memory.md",
            content="智能指针管理对象生命周期",
            embedding=(1.0, 0.0, 0.0),
        ),
        _record(
            chunk_id="chunk-network",
            document_id="doc-network",
            namespace="projects",
            path="project/network.md",
            content="事件循环处理网络连接",
            embedding=(0.0, 1.0, 0.0),
        ),
    )

    with ChromaVectorStore(path) as store:
        store.initialize(profile)
        store.upsert(records)

        assert store.count() == 2
        assert store.profile_fingerprint() == profile.fingerprint

        results = store.search((1.0, 0.0, 0.0), top_k=2)
        assert results[0].chunk_id == "chunk-smart-pointer"
        assert results[0].relative_path == Path("cpp/memory.md")
        assert results[0].heading_path == ("一级标题", "二级标题")
        assert (results[0].start_line, results[0].end_line) == (3, 5)
        assert results[0].content == "智能指针管理对象生命周期"
        assert results[0].score == pytest.approx(1.0)

        filtered = store.search(
            (1.0, 0.0, 0.0),
            top_k=2,
            source_namespace="projects",
        )
        assert [result.chunk_id for result in filtered] == ["chunk-network"]

        store.delete_documents(("doc-cpp", "missing-doc"))
        assert store.count() == 1

    # 新实例模拟进程重启；上一个 with 会先关闭文件句柄，Windows 才能安全清理。
    with ChromaVectorStore(path) as restarted:
        restarted.initialize(profile)
        assert restarted.count() == 1
        assert restarted.search((0.0, 1.0, 0.0), top_k=1)[0].chunk_id == (
            "chunk-network"
        )


def test_reset_replaces_old_profile_and_all_records(
    temporary_directory: Path,
) -> None:
    with ChromaVectorStore(temporary_directory / "vectors") as store:
        first_profile = _profile()
        store.initialize(first_profile)
        store.upsert(
            (
                _record(
                    chunk_id="old",
                    document_id="old-doc",
                    namespace="notes",
                    path="old.md",
                    content="旧内容",
                    embedding=(1.0, 0.0, 0.0),
                ),
            )
        )

        second_profile = _profile(model_name="test-embedding-v2", dimension=2)
        store.reset(second_profile)

        assert store.count() == 0
        assert store.profile_fingerprint() == second_profile.fingerprint


def test_rejects_wrong_vector_dimension_and_use_after_close(
    temporary_directory: Path,
) -> None:
    store = ChromaVectorStore(temporary_directory / "vectors")
    store.initialize(_profile())

    wrong_dimension = _record(
        chunk_id="invalid",
        document_id="doc",
        namespace="notes",
        path="invalid.md",
        content="正文",
        embedding=(1.0, 0.0),
    )
    with pytest.raises(ValueError, match="dimension"):
        store.upsert((wrong_dimension,))

    store.close()
    with pytest.raises(ChromaVectorStoreError, match="closed"):
        store.count()
