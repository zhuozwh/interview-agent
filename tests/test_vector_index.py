"""验证从 Markdown 到 Chroma 检索的增量闭环和失败恢复。"""

from collections.abc import Iterator, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.retrieval import (
    EmbeddingProviderError,
    VectorIndexProfileError,
    VectorSearchInputError,
    build_index_plan,
    load_markdown_documents,
    prepare_index_documents,
    search_chunks,
    synchronize_vector_index,
)
from interview_agent.storage import (
    ChromaVectorStore,
    SQLiteDatabase,
    SQLiteIndexStateStore,
)


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """Markdown、SQLite 和 Chroma 都写入自动清理的同一临时根目录。"""
    with TemporaryDirectory(prefix="interview-agent-vector-index-test-") as directory:
        yield Path(directory)


class KeywordEmbedding:
    """按少量测试关键词生成确定向量，证明流程而不冒充真实语义模型。"""

    dimension = 3

    def __init__(self, model_name: str = "test-keyword-v1") -> None:
        self.model_name = model_name
        self.calls: list[tuple[str, ...]] = []

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(tuple(texts))
        vectors: list[list[float]] = []
        for text in texts:
            if "智能指针" in text or "内存" in text:
                vectors.append([1.0, 0.05, 0.05])
            elif "网络" in text or "事件循环" in text:
                vectors.append([0.05, 1.0, 0.05])
            else:
                vectors.append([0.05, 0.05, 1.0])
        return vectors

    def embed_query(self, query: str) -> list[float]:
        """查询走独立接口，但测试向量规则与文档保持一致。"""
        return self.embed_texts((query,))[0]


class FailingEmbedding(KeywordEmbedding):
    """模拟远程供应方超时。"""

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        raise TimeoutError("simulated timeout")

    def embed_query(self, query: str) -> list[float]:
        raise TimeoutError("simulated timeout")


class FailOnceStateStore:
    """模拟向量写入成功后 SQLite 首次提交失败。"""

    def __init__(self, delegate: SQLiteIndexStateStore) -> None:
        self.delegate = delegate
        self.should_fail = True

    def load_vector_profile(self):
        return self.delegate.load_vector_profile()

    def apply_plan(self, plan, *, vector_profile=None) -> None:
        if self.should_fail:
            self.should_fail = False
            raise RuntimeError("simulated SQLite failure")
        self.delegate.apply_plan(plan, vector_profile=vector_profile)


def _prepare(source_directory: Path):
    """执行真实 Markdown 加载、Front Matter 分离、切分和指纹生成。"""
    documents = load_markdown_documents(
        source_directory,
        [source_directory.parent],
    )
    return prepare_index_documents(
        documents,
        max_chunk_characters=1000,
        source_namespace="notes",
    )


def _create_stores(temporary_directory: Path):
    """创建已初始化的 SQLite 仓储和 Chroma 适配器。"""
    state_store = SQLiteIndexStateStore(
        SQLiteDatabase(temporary_directory / "state.db")
    )
    state_store.initialize()
    vector_store = ChromaVectorStore(temporary_directory / "vectors")
    return state_store, vector_store


def test_initial_sync_search_and_unchanged_cycle_skip_embedding(
    temporary_directory: Path,
) -> None:
    source = temporary_directory / "allowed" / "notes"
    source.mkdir(parents=True)
    (source / "memory.md").write_text(
        "---\ntype: note\n---\n# C++ 内存\n智能指针管理对象生命周期",
        encoding="utf-8",
    )
    (source / "network.md").write_text(
        "# 网络\n事件循环处理网络连接",
        encoding="utf-8",
    )
    state_store, vector_store = _create_stores(temporary_directory)
    provider = KeywordEmbedding()

    with vector_store:
        current = _prepare(source)
        first_plan = build_index_plan(current, state_store.load_document_states())
        first_report = synchronize_vector_index(
            first_plan,
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
            batch_size=1,
        )

        assert first_report.profile_rebuilt is True
        assert first_report.embedded_document_count == 2
        assert first_report.embedded_chunk_count == 2
        assert vector_store.count() == 2
        assert state_store.load_vector_profile() is not None

        provider.calls.clear()
        unchanged_plan = build_index_plan(
            _prepare(source),
            state_store.load_document_states(),
        )
        unchanged_report = synchronize_vector_index(
            unchanged_plan,
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
        )

        assert unchanged_report.profile_rebuilt is False
        assert unchanged_report.embedded_chunk_count == 0
        assert unchanged_report.unchanged_document_count == 2
        assert provider.calls == []

        results = search_chunks(
            "智能指针如何管理内存",
            top_k=1,
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
            source_namespace="notes",
        )
        assert len(results) == 1
        assert results[0].relative_path == Path("memory.md")
        assert results[0].heading_path == ("C++ 内存",)
        assert (results[0].start_line, results[0].end_line) == (4, 5)
        assert results[0].score > 0.9


def test_modified_and_deleted_documents_replace_vector_records(
    temporary_directory: Path,
) -> None:
    source = temporary_directory / "allowed" / "notes"
    source.mkdir(parents=True)
    memory_path = source / "memory.md"
    network_path = source / "network.md"
    memory_path.write_text("# 内存\n旧内容", encoding="utf-8")
    network_path.write_text("# 网络\n事件循环", encoding="utf-8")
    state_store, vector_store = _create_stores(temporary_directory)
    provider = KeywordEmbedding()

    with vector_store:
        initial = _prepare(source)
        synchronize_vector_index(
            build_index_plan(initial, ()),
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
        )

        # 修改后的文档减少为一个片段，同时删除另一篇文档。
        memory_path.write_text("# 内存\n智能指针新内容", encoding="utf-8")
        network_path.unlink()
        current = _prepare(source)
        plan = build_index_plan(current, state_store.load_document_states())
        provider.calls.clear()

        report = synchronize_vector_index(
            plan,
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
        )

        assert len(plan.modified) == 1
        assert len(plan.deleted) == 1
        assert report.embedded_document_count == 1
        assert report.deleted_document_count == 1
        assert vector_store.count() == sum(len(item.chunks) for item in current)
        assert len(provider.calls) == 1
        assert vector_store.search((1.0, 0.05, 0.05), top_k=10)[
            0
        ].relative_path == Path("memory.md")


def test_model_change_forces_all_unchanged_documents_to_rebuild(
    temporary_directory: Path,
) -> None:
    source = temporary_directory / "allowed" / "notes"
    source.mkdir(parents=True)
    (source / "note.md").write_text("# 标题\n正文", encoding="utf-8")
    state_store, vector_store = _create_stores(temporary_directory)

    with vector_store:
        current = _prepare(source)
        first_provider = KeywordEmbedding("test-keyword-v1")
        synchronize_vector_index(
            build_index_plan(current, ()),
            embedding_provider=first_provider,
            vector_store=vector_store,
            state_store=state_store,
        )

        unchanged_plan = build_index_plan(
            current,
            state_store.load_document_states(),
        )
        second_provider = KeywordEmbedding("test-keyword-v2")
        report = synchronize_vector_index(
            unchanged_plan,
            embedding_provider=second_provider,
            vector_store=vector_store,
            state_store=state_store,
        )

        assert len(unchanged_plan.unchanged) == 1
        assert report.profile_rebuilt is True
        assert report.embedded_document_count == 1
        assert len(second_provider.calls) == 1
        assert state_store.load_vector_profile().embedding_model == (
            "test-keyword-v2"
        )


def test_embedding_failure_does_not_advance_sqlite_state(
    temporary_directory: Path,
) -> None:
    source = temporary_directory / "allowed" / "notes"
    source.mkdir(parents=True)
    (source / "note.md").write_text("# 标题\n正文", encoding="utf-8")
    state_store, vector_store = _create_stores(temporary_directory)

    with vector_store:
        plan = build_index_plan(_prepare(source), ())
        with pytest.raises(EmbeddingProviderError):
            synchronize_vector_index(
                plan,
                embedding_provider=FailingEmbedding(),
                vector_store=vector_store,
                state_store=state_store,
            )

        assert state_store.load_document_states() == ()
        assert state_store.load_vector_profile() is None
        assert vector_store.count() == 0


def test_sqlite_failure_can_be_retried_idempotently(
    temporary_directory: Path,
) -> None:
    source = temporary_directory / "allowed" / "notes"
    source.mkdir(parents=True)
    (source / "note.md").write_text("# 内存\n智能指针", encoding="utf-8")
    state_store, vector_store = _create_stores(temporary_directory)
    fail_once_store = FailOnceStateStore(state_store)
    provider = KeywordEmbedding()
    plan = build_index_plan(_prepare(source), ())

    with vector_store:
        with pytest.raises(RuntimeError, match="SQLite failure"):
            synchronize_vector_index(
                plan,
                embedding_provider=provider,
                vector_store=vector_store,
                state_store=fail_once_store,
            )

        # 向量已经写入但 SQLite 尚未推进；相同计划重试会安全重建并最终一致。
        assert vector_store.count() == 1
        assert state_store.load_document_states() == ()
        synchronize_vector_index(
            plan,
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=fail_once_store,
        )
        assert vector_store.count() == 1
        assert len(state_store.load_document_states()) == 1


def test_search_rejects_invalid_input_and_profile_mismatch(
    temporary_directory: Path,
) -> None:
    state_store, vector_store = _create_stores(temporary_directory)
    provider = KeywordEmbedding()

    with vector_store:
        with pytest.raises(VectorSearchInputError, match="query"):
            search_chunks(
                " ",
                top_k=1,
                embedding_provider=provider,
                vector_store=vector_store,
                state_store=state_store,
            )

        with pytest.raises(VectorSearchInputError, match="top_k"):
            search_chunks(
                "问题",
                top_k=0,
                embedding_provider=provider,
                vector_store=vector_store,
                state_store=state_store,
            )

        with pytest.raises(VectorIndexProfileError, match="not ready"):
            search_chunks(
                "问题",
                top_k=1,
                embedding_provider=provider,
                vector_store=vector_store,
                state_store=state_store,
            )
