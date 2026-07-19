"""验证 Markdown 标题识别、确定性切分和原文位置映射。"""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.retrieval import (
    MarkdownDocument,
    load_markdown_documents,
    split_markdown_document,
    split_markdown_documents,
)


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """提供自动清理的临时目录，不依赖 pytest tmp_path。"""
    with TemporaryDirectory(prefix="interview-agent-chunking-test-") as directory:
        yield Path(directory)


def _document(content: str, relative_path: str = "note.md") -> MarkdownDocument:
    """构造只用于纯切分测试的最小文档。"""
    relative = Path(relative_path)
    return MarkdownDocument(
        source_path=Path("C:/test-vault") / relative,
        relative_path=relative,
        content=content,
    )


def test_splits_atx_headings_and_preserves_hierarchy_and_line_ranges() -> None:
    content = (
        "前言\n"
        "# 一级\n"
        "一级正文\n"
        "## 二级\n"
        "二级正文\n"
        "### 三级\n"
        "三级正文\n"
        "## 同级 ##\n"
        "同级正文\n"
    )

    chunks = split_markdown_document(
        _document(content), max_chunk_characters=1000
    )

    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2, 3, 4]
    assert [chunk.heading_path for chunk in chunks] == [
        (),
        ("一级",),
        ("一级", "二级"),
        ("一级", "二级", "三级"),
        ("一级", "同级"),
    ]
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (1, 1),
        (2, 3),
        (4, 5),
        (6, 7),
        (8, 9),
    ]
    assert "".join(chunk.content for chunk in chunks) == content


def test_does_not_treat_hashes_inside_fenced_code_as_headings() -> None:
    content = (
        "# 顶层\n"
        "```cpp\n"
        "# 代码中的注释\n"
        "```\n"
        "~~~text\n"
        "## 仍然是代码\n"
        "~~~\n"
        "正文\n"
        "## 子标题\n"
        "子正文\n"
    )

    chunks = split_markdown_document(
        _document(content), max_chunk_characters=1000
    )

    assert len(chunks) == 2
    assert chunks[0].heading_path == ("顶层",)
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 8
    assert "## 仍然是代码" in chunks[0].content
    assert chunks[1].heading_path == ("顶层", "子标题")
    assert (chunks[1].start_line, chunks[1].end_line) == (9, 10)


def test_heading_level_jump_does_not_create_fake_parent() -> None:
    content = "# 一级\n### 三级\n正文\n"

    chunks = split_markdown_document(
        _document(content), max_chunk_characters=1000
    )

    assert [chunk.heading_path for chunk in chunks] == [
        ("一级",),
        ("一级", "三级"),
    ]


def test_keeps_heading_only_section() -> None:
    content = "# 只有标题\n## 子标题\n正文\n"

    chunks = split_markdown_document(
        _document(content), max_chunk_characters=1000
    )

    assert [chunk.heading_path for chunk in chunks] == [
        ("只有标题",),
        ("只有标题", "子标题"),
    ]
    assert chunks[0].content == "# 只有标题\n"
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 1)


def test_hash_without_following_space_is_not_an_atx_heading() -> None:
    content = "#不是标题\n正文"

    chunks = split_markdown_document(
        _document(content), max_chunk_characters=1000
    )

    assert len(chunks) == 1
    assert chunks[0].heading_path == ()
    assert chunks[0].content == content


def test_document_without_heading_keeps_empty_heading_path() -> None:
    content = "第一行\n第二行"

    chunks = split_markdown_document(
        _document(content), max_chunk_characters=1000
    )

    assert len(chunks) == 1
    assert chunks[0].heading_path == ()
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 2)
    assert chunks[0].content == content


def test_whitespace_only_document_returns_no_chunks() -> None:
    assert split_markdown_document(_document(" \n\n\t")) == []


def test_prefers_paragraph_boundaries_when_section_exceeds_limit() -> None:
    content = "甲乙\n\n丙丁\n"

    chunks = split_markdown_document(
        _document(content), max_chunk_characters=5
    )

    assert [chunk.content for chunk in chunks] == ["甲乙\n\n", "丙丁\n"]
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (1, 2),
        (3, 3),
    ]
    assert all(len(chunk.content) <= 5 for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == content


def test_hard_splits_a_single_long_line_and_keeps_its_line_number() -> None:
    chunks = split_markdown_document(
        _document("abcdefghijk"), max_chunk_characters=5
    )

    assert [chunk.content for chunk in chunks] == ["abcde", "fghij", "k"]
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (1, 1),
        (1, 1),
        (1, 1),
    ]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]


def test_splits_multiple_documents_in_input_order_and_resets_indexes() -> None:
    documents = [
        _document("# A\n正文 A", "a.md"),
        _document("# B\n正文 B", "b.md"),
    ]

    chunks = split_markdown_documents(documents, max_chunk_characters=1000)

    assert [chunk.relative_path for chunk in chunks] == [Path("a.md"), Path("b.md")]
    assert [chunk.heading_path for chunk in chunks] == [("A",), ("B",)]
    assert [chunk.chunk_index for chunk in chunks] == [0, 0]


def test_rejects_non_positive_chunk_limit() -> None:
    with pytest.raises(ValueError, match="must be greater than zero"):
        split_markdown_document(_document("content"), max_chunk_characters=0)

    with pytest.raises(ValueError, match="must be greater than zero"):
        split_markdown_documents([], max_chunk_characters=-1)


def test_loads_and_splits_temporary_markdown_files_in_stable_order(
    temporary_directory: Path,
) -> None:
    # 这条链路测试从真实文件读取开始，但所有文件都位于自动清理的临时目录。
    source_directory = temporary_directory / "allowed" / "notes"
    nested_directory = source_directory / "nested"
    nested_directory.mkdir(parents=True)
    (source_directory / "b.md").write_text("# B\n乙", encoding="utf-8")
    (nested_directory / "a.md").write_text("# A\n甲", encoding="utf-8")

    documents = load_markdown_documents(
        source_directory, [temporary_directory / "allowed"]
    )
    chunks = split_markdown_documents(documents, max_chunk_characters=1000)

    assert [chunk.relative_path.as_posix() for chunk in chunks] == [
        "b.md",
        "nested/a.md",
    ]
    assert [chunk.heading_path for chunk in chunks] == [("B",), ("A",)]
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (1, 2),
        (1, 2),
    ]
