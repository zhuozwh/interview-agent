"""验证 Front Matter 原样分离、错误处理和正文行号偏移。"""

from pathlib import Path

import pytest

from interview_agent.retrieval import (
    MarkdownDocument,
    MarkdownFrontMatterError,
    reconstruct_document_content,
    separate_front_matter,
    split_markdown_document,
)


def _document(content: str) -> MarkdownDocument:
    """构造不访问真实文件系统的测试文档。"""
    return MarkdownDocument(
        source_path=Path("C:/test-vault/note.md"),
        relative_path=Path("note.md"),
        content=content,
    )


def test_separates_front_matter_exactly_and_preserves_body_line_offset() -> None:
    content = "---\r\ntype: note\r\ntags: [cpp]\r\n---\r\n# 标题\r\n正文"

    parsed = separate_front_matter(_document(content))

    assert parsed.front_matter == "---\r\ntype: note\r\ntags: [cpp]\r\n---\r\n"
    assert parsed.content == "# 标题\r\n正文"
    assert parsed.content_start_line == 5
    assert reconstruct_document_content(parsed) == content

    chunks = split_markdown_document(parsed, max_chunk_characters=1000)
    assert len(chunks) == 1
    assert (chunks[0].start_line, chunks[0].end_line) == (5, 6)
    assert chunks[0].heading_path == ("标题",)


def test_document_without_front_matter_is_returned_unchanged() -> None:
    document = _document("# 标题\n正文")

    parsed = separate_front_matter(document)

    assert parsed is document
    assert parsed.front_matter is None
    assert parsed.content_start_line == 1


def test_indented_horizontal_rule_is_not_front_matter() -> None:
    document = _document("  ---\n普通正文\n---\n")

    parsed = separate_front_matter(document)

    assert parsed is document
    assert parsed.front_matter is None


def test_accepts_yaml_ellipsis_as_closing_delimiter() -> None:
    content = "---\ntype: note\n...\n正文"

    parsed = separate_front_matter(_document(content))

    assert parsed.front_matter == "---\ntype: note\n...\n"
    assert parsed.content == "正文"
    assert parsed.content_start_line == 4
    assert reconstruct_document_content(parsed) == content


def test_separation_is_idempotent() -> None:
    parsed = separate_front_matter(_document("---\ntype: note\n---\n正文"))

    assert separate_front_matter(parsed) is parsed


def test_unclosed_front_matter_returns_clear_file_error() -> None:
    document = _document("---\ntype: note\n# 正文")

    with pytest.raises(
        MarkdownFrontMatterError, match="Front Matter is not closed"
    ) as error_info:
        separate_front_matter(document)

    assert error_info.value.source_path == document.source_path
