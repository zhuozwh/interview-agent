"""使用真实本地 Embedding 运行脱敏的检索效果验收。"""

import os
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.rag import RagContextStatus, build_search_notes_context
from interview_agent.retrieval import (
    FastEmbedEmbeddingProvider,
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

_RUN_REAL_ACCEPTANCE = os.getenv("RUN_REAL_EMBEDDING_ACCEPTANCE") == "1"


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """正文、SQLite 和 Chroma 数据都放入自动清理的独立目录。"""
    with TemporaryDirectory(
        prefix="interview-agent-real-retrieval-test-"
    ) as directory:
        yield Path(directory)


@pytest.mark.skipif(
    not _RUN_REAL_ACCEPTANCE,
    reason="set RUN_REAL_EMBEDDING_ACCEPTANCE=1 to load the real local model",
)
def test_real_embedding_accepts_known_topics_and_rejects_hard_negatives(
    temporary_directory: Path,
) -> None:
    """固定正例必须找到期望来源，技术型库外问题必须明确无结果。"""
    source = temporary_directory / "allowed" / "notes"
    source.mkdir(parents=True)
    documents = {
        "memory.md": (
            "# 智能指针\n"
            "shared_ptr 使用引用计数管理共享对象。两个对象互相持有 "
            "shared_ptr 会形成循环引用，应把非拥有关系改为 weak_ptr。\n"
        ),
        "epoll.md": (
            "# epoll 触发模式\n"
            "LT 是水平触发，只要缓冲区仍有数据就继续通知；ET 是边缘触发，"
            "状态变化时通知，因此通常配合非阻塞套接字并一次读到 EAGAIN。\n"
        ),
        "tcp.md": (
            "# TCP 三次握手\n"
            "客户端发送 SYN，服务端返回 SYN 和 ACK，客户端再确认 ACK。"
            "三次握手用于确认双方的收发能力并同步初始序列号。\n"
        ),
        "polymorphism.md": (
            "# C++ 动态多态\n"
            "含虚函数的对象通常保存虚表指针，运行时通过虚函数表选择实际"
            "派生类函数，从而实现动态绑定。\n"
        ),
        "deadlock.md": (
            "# 死锁条件\n"
            "死锁的四个必要条件是互斥、请求并保持、不可剥夺和循环等待。"
            "破坏其中任意一个条件都可以预防死锁。\n"
        ),
    }
    for relative_path, content in documents.items():
        (source / relative_path).write_text(content, encoding="utf-8")

    loaded = load_markdown_documents(source, (source.parent,))
    indexed = prepare_index_documents(
        loaded,
        max_chunk_characters=500,
        source_namespace="notes",
    )

    cache_directory = Path(
        os.getenv("EMBEDDING_CACHE_DIRECTORY", "embedding_models")
    )
    provider = FastEmbedEmbeddingProvider(
        cache_directory=cache_directory,
        local_files_only=True,
    )
    database = SQLiteDatabase(temporary_directory / "state.sqlite3")
    state_store = SQLiteIndexStateStore(database)
    trace_store = SQLiteToolTraceStore(database)
    state_store.initialize()
    trace_store.initialize()

    with ChromaVectorStore(temporary_directory / "chroma") as vector_store:
        synchronize_vector_index(
            build_index_plan(indexed, ()),
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
        )
        tool = SearchNotesTool(
            embedding_provider=provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
        )

        positive_cases = (
            ("shared_ptr 循环引用为什么要使用 weak_ptr？", "memory.md"),
            ("epoll 的 ET 和 LT 模式有什么区别？", "epoll.md"),
            ("TCP 为什么需要三次握手？", "tcp.md"),
            ("C++ 虚函数表如何实现动态多态？", "polymorphism.md"),
            ("死锁的四个必要条件是什么？", "deadlock.md"),
        )
        for query, expected_path in positive_cases:
            response = tool.execute(
                SearchNotesRequest(query=query, top_k=5)
            )
            assert response.status is SearchNotesStatus.SUCCESS
            assert expected_path in {
                result.relative_path for result in response.results
            }
            context = build_search_notes_context(response)
            assert context.status is RagContextStatus.READY
            assert expected_path in {
                citation.relative_path for citation in context.citations
            }
            assert len(context.rendered_context) <= 8_000

        negative_queries = (
            "SwiftUI 中 @StateObject 的生命周期是什么？",
            "Kubernetes Ingress TLS 证书如何自动轮换？",
            "量子纠错中表面码的阈值是什么？",
            "React useEffect 的 cleanup 什么时候执行？",
            "Unity ShaderLab 中 SubShader 有什么作用？",
            "Rust 借用检查器如何阻止数据竞争？",
        )
        for query in negative_queries:
            response = tool.execute(
                SearchNotesRequest(query=query, top_k=5)
            )
            assert response.status is SearchNotesStatus.NO_RESULTS
            assert response.results == ()
            context = build_search_notes_context(response)
            assert context.status is RagContextStatus.NO_EVIDENCE
            assert context.citations == ()
