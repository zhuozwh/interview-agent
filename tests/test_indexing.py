"""验证稳定索引标识、SHA-256 指纹和增量计划分类。"""

from pathlib import Path

import pytest

from interview_agent.retrieval import (
    MarkdownDocument,
    StoredDocumentState,
    build_index_plan,
    prepare_index_document,
    prepare_index_documents,
)


def _document(content: str, relative_path: str = "note.md") -> MarkdownDocument:
    """构造可重复的索引测试文档。"""
    relative = Path(relative_path)
    return MarkdownDocument(
        source_path=Path("C:/test-vault") / relative,
        relative_path=relative,
        content=content,
    )


def _stored(document) -> StoredDocumentState:
    """把当前索引记录转换为模拟的 SQLite 文档状态。"""
    return StoredDocumentState(
        document_id=document.document_id,
        source_namespace=document.source_namespace,
        relative_path=document.relative_path,
        content_fingerprint=document.content_fingerprint,
        index_fingerprint=document.index_fingerprint,
        front_matter_present=document.front_matter_present,
        chunk_count=len(document.chunks),
    )


def test_prepared_index_records_are_stable_and_exclude_front_matter_from_chunks() -> None:
    document = _document("---\ntype: note\n---\n# 标题\n正文")

    first = prepare_index_document(document, max_chunk_characters=1000)
    second = prepare_index_document(document, max_chunk_characters=1000)

    assert first == second
    assert first.front_matter_present is True
    assert len(first.document_id) == 64
    assert len(first.content_fingerprint) == 64
    assert len(first.index_fingerprint) == 64
    assert len(first.chunks) == 1
    assert first.chunks[0].source_namespace == "markdown"
    assert first.chunks[0].content == "# 标题\n正文"
    assert "type: note" not in first.chunks[0].content
    assert (first.chunks[0].start_line, first.chunks[0].end_line) == (4, 5)
    assert len(first.chunks[0].chunk_id) == 64
    assert len(first.chunks[0].fingerprint) == 64


def test_document_identity_depends_on_namespace_and_relative_path() -> None:
    first = prepare_index_document(_document("正文", "a.md"))
    renamed = prepare_index_document(_document("正文", "b.md"))
    other_namespace = prepare_index_document(
        _document("正文", "a.md"), source_namespace="resume"
    )

    assert first.document_id != renamed.document_id
    assert first.document_id != other_namespace.document_id
    assert first.content_fingerprint == renamed.content_fingerprint


def test_content_change_keeps_ids_but_changes_fingerprints() -> None:
    before = prepare_index_document(_document("# 标题\n旧正文"))
    after = prepare_index_document(_document("# 标题\n新正文"))

    assert before.document_id == after.document_id
    assert before.content_fingerprint != after.content_fingerprint
    assert before.index_fingerprint != after.index_fingerprint
    assert before.chunks[0].chunk_id == after.chunks[0].chunk_id
    assert before.chunks[0].fingerprint != after.chunks[0].fingerprint


def test_chunk_configuration_change_is_detected_by_index_fingerprint() -> None:
    document = _document("第一段\n\n第二段\n")

    small = prepare_index_document(document, max_chunk_characters=5)
    large = prepare_index_document(document, max_chunk_characters=100)

    assert small.content_fingerprint == large.content_fingerprint
    assert small.index_fingerprint != large.index_fingerprint
    assert len(small.chunks) > len(large.chunks)


def test_build_index_plan_classifies_changes_in_stable_order() -> None:
    unchanged = prepare_index_document(_document("不变", "a.md"))
    modified_before = prepare_index_document(_document("旧", "b.md"))
    modified_after = prepare_index_document(_document("新", "b.md"))
    added = prepare_index_document(_document("新增", "c.md"))
    deleted = prepare_index_document(_document("删除", "d.md"))

    plan = build_index_plan(
        [added, modified_after, unchanged],
        [_stored(deleted), _stored(unchanged), _stored(modified_before)],
    )

    assert [item.relative_path.as_posix() for item in plan.added] == ["c.md"]
    assert [item.relative_path.as_posix() for item in plan.modified] == ["b.md"]
    assert [item.relative_path.as_posix() for item in plan.unchanged] == ["a.md"]
    assert [item.relative_path.as_posix() for item in plan.deleted] == ["d.md"]
    assert plan.change_count == 3


def test_rejects_invalid_identity_inputs_and_duplicates() -> None:
    with pytest.raises(ValueError, match="source_namespace"):
        prepare_index_document(_document("正文"), source_namespace=" ")

    with pytest.raises(ValueError, match="Invalid relative"):
        prepare_index_document(_document("正文", "../outside.md"))

    document = _document("正文")
    with pytest.raises(ValueError, match="Duplicate Markdown document identity"):
        prepare_index_documents([document, document])
