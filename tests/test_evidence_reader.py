"""对抗性验证本地 Markdown 引用读取器的路径和资源边界。"""

from pathlib import Path

import pytest

from interview_agent.retrieval import (
    LocalEvidenceReadError,
    read_local_markdown_evidence,
)


def _source_root(tmp_path: Path) -> tuple[Path, Path]:
    """建立一个合成白名单和其中的 notes 根。"""
    allowed = tmp_path / "knowledge"
    source = allowed / "interview"
    source.mkdir(parents=True)
    return allowed, source


def _read(
    source: Path,
    allowed: Path,
    relative_path: str,
    start_line: int,
    end_line: int,
    **limits,
):
    """用较小默认上限调用真实读取器。"""
    return read_local_markdown_evidence(
        source,
        (allowed,),
        relative_path,
        start_line,
        end_line,
        max_file_size_bytes=limits.pop("max_file_size_bytes", 1024),
        **limits,
    )


def test_reader_returns_exact_current_lines_as_uninterpreted_text(
    tmp_path: Path,
) -> None:
    """HTML 形态的原文保持原样，读取范围与当前行号一致。"""
    allowed, source = _source_root(tmp_path)
    document = source / "cpp" / "raii.md"
    document.parent.mkdir()
    document.write_text(
        "# RAII\n\n<script>alert('source-only')</script>\n资源跟随对象\n结束",
        encoding="utf-8",
    )

    excerpt = _read(source, allowed, "cpp/raii.md", 3, 4)

    assert excerpt.content == (
        "<script>alert('source-only')</script>\n资源跟随对象"
    )
    assert excerpt.start_line == 3
    assert excerpt.end_line == 4
    assert excerpt.truncated is False


def test_reader_reports_actual_end_line_after_character_truncation(
    tmp_path: Path,
) -> None:
    """字符预算截在中间行时不能声称展示了后续未返回行。"""
    allowed, source = _source_root(tmp_path)
    (source / "bounded.md").write_text(
        "abcd\nefgh\nijkl",
        encoding="utf-8",
    )

    excerpt = _read(
        source,
        allowed,
        "bounded.md",
        1,
        3,
        max_excerpt_characters=7,
    )

    assert excerpt.content == "abcd\nef"
    assert excerpt.end_line == 2
    assert excerpt.truncated is True


@pytest.mark.parametrize(
    "relative_path",
    (
        "../outside.md",
        "/absolute.md",
        "D:/private.md",
        "folder\\escape.md",
        "folder/./note.md",
        "folder//note.md",
        "note.txt",
        "",
    ),
)
def test_reader_rejects_forged_or_non_markdown_paths(
    tmp_path: Path,
    relative_path: str,
) -> None:
    """客户端或损坏历史不能把读取器变成任意文件接口。"""
    allowed, source = _source_root(tmp_path)
    safe_document = source / "folder" / "note.md"
    safe_document.parent.mkdir()
    safe_document.write_text("safe", encoding="utf-8")

    with pytest.raises(LocalEvidenceReadError):
        _read(source, allowed, relative_path, 1, 1)


@pytest.mark.parametrize(
    ("start_line", "end_line"),
    ((0, 1), (2, 1), (True, 2), (1, 10_002)),
)
def test_reader_rejects_invalid_line_ranges(
    tmp_path: Path,
    start_line,
    end_line,
) -> None:
    """行号不能关闭正整数和有界范围约束。"""
    allowed, source = _source_root(tmp_path)
    (source / "note.md").write_text("line", encoding="utf-8")

    with pytest.raises(LocalEvidenceReadError):
        _read(source, allowed, "note.md", start_line, end_line)


def test_reader_rejects_missing_stale_large_and_invalid_utf8_sources(
    tmp_path: Path,
) -> None:
    """文件漂移和异常字节只返回稳定读取失败，不放宽大小限制。"""
    allowed, source = _source_root(tmp_path)
    (source / "short.md").write_text("only one line", encoding="utf-8")
    (source / "large.md").write_text("x" * 20, encoding="utf-8")
    (source / "invalid.md").write_bytes(b"\xff\xfe")

    with pytest.raises(LocalEvidenceReadError):
        _read(source, allowed, "missing.md", 1, 1)
    with pytest.raises(LocalEvidenceReadError):
        _read(source, allowed, "short.md", 2, 2)
    with pytest.raises(LocalEvidenceReadError):
        _read(
            source,
            allowed,
            "large.md",
            1,
            1,
            max_file_size_bytes=8,
        )
    with pytest.raises(LocalEvidenceReadError):
        _read(source, allowed, "invalid.md", 1, 1)


def test_reader_rejects_symlink_that_resolves_outside_allowlist(
    tmp_path: Path,
) -> None:
    """即使相对路径看似正常，符号链接的真实目标也不能越界。"""
    allowed, source = _source_root(tmp_path)
    outside = tmp_path / "private.md"
    outside.write_text("private-source", encoding="utf-8")
    link = source / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"当前 Windows 环境不能创建测试符号链接：{error}")

    with pytest.raises(LocalEvidenceReadError):
        _read(source, allowed, "linked.md", 1, 1)
