"""按已登记引用位置安全读取当前 Markdown 原文片段。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


class LocalEvidenceReadError(RuntimeError):
    """本地证据无法在既定边界内读取。"""


@dataclass(frozen=True, slots=True)
class LocalEvidenceExcerpt:
    """当前只读文件中经过大小限制的引用原文。"""

    content: str
    start_line: int
    end_line: int
    truncated: bool


def read_local_markdown_evidence(
    source_directory: Path,
    allowed_directories: Iterable[Path],
    relative_path: str,
    start_line: int,
    end_line: int,
    *,
    max_file_size_bytes: int,
    max_excerpt_lines: int = 80,
    max_excerpt_characters: int = 6_000,
) -> LocalEvidenceExcerpt:
    """只在固定数据源和白名单内读取引用行，不接受任意绝对路径。"""
    _require_positive_limit(max_file_size_bytes, "max_file_size_bytes")
    _require_positive_limit(max_excerpt_lines, "max_excerpt_lines")
    _require_positive_limit(max_excerpt_characters, "max_excerpt_characters")
    _require_line_range(start_line, end_line)
    relative_parts = _safe_relative_parts(relative_path)
    allowed = tuple(_resolve_directory(path) for path in allowed_directories)
    if not allowed:
        raise LocalEvidenceReadError("Allowed data directories are empty.")
    source_root = _resolve_directory(source_directory)
    if not _is_within_any(source_root, allowed):
        raise LocalEvidenceReadError("Evidence source is outside the allowlist.")

    candidate = source_root.joinpath(*relative_parts)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise LocalEvidenceReadError("Evidence source cannot be resolved.") from error
    if (
        not resolved.is_file()
        or resolved.suffix.casefold() != ".md"
        or not resolved.is_relative_to(source_root)
        or not _is_within_any(resolved, allowed)
    ):
        raise LocalEvidenceReadError("Evidence source is outside the readable boundary.")

    try:
        with resolved.open("rb") as file:
            content_bytes = file.read(max_file_size_bytes + 1)
    except OSError as error:
        raise LocalEvidenceReadError("Evidence source cannot be read.") from error
    if len(content_bytes) > max_file_size_bytes:
        raise LocalEvidenceReadError("Evidence source exceeds the file-size limit.")
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LocalEvidenceReadError("Evidence source is not valid UTF-8.") from error

    lines = content.splitlines()
    if start_line > len(lines) or end_line > len(lines):
        raise LocalEvidenceReadError("Evidence location no longer exists.")
    requested = lines[start_line - 1 : end_line]
    displayed = requested[:max_excerpt_lines]
    truncated = len(displayed) < len(requested)
    excerpt = "\n".join(displayed)
    if len(excerpt) > max_excerpt_characters:
        excerpt = excerpt[:max_excerpt_characters]
        truncated = True
    # 字符截断可能发生在中间行，返回的末行必须描述实际展示范围。
    displayed_line_count = len(excerpt.splitlines()) if excerpt else 1
    displayed_end_line = start_line + max(0, displayed_line_count - 1)
    return LocalEvidenceExcerpt(
        content=excerpt,
        start_line=start_line,
        end_line=displayed_end_line,
        truncated=truncated,
    )


def _safe_relative_parts(relative_path: object) -> tuple[str, ...]:
    """把历史引用规范为 POSIX 分段，并拒绝驱动器、控制符和越界。"""
    if (
        not isinstance(relative_path, str)
        or not relative_path.strip()
        or len(relative_path) > 1_024
        or "\0" in relative_path
        or "\\" in relative_path
        or ":" in relative_path
    ):
        raise LocalEvidenceReadError("Evidence path is invalid.")
    raw_parts = tuple(relative_path.split("/"))
    path = PurePosixPath(relative_path)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
        or tuple(path.parts) != raw_parts
    ):
        raise LocalEvidenceReadError("Evidence path is not a safe relative path.")
    if path.suffix.casefold() != ".md":
        raise LocalEvidenceReadError("Evidence source must be Markdown.")
    return raw_parts


def _resolve_directory(path: Path) -> Path:
    """规范化一个必须存在的目录。"""
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise LocalEvidenceReadError("Evidence directory cannot be resolved.") from error
    if not resolved.is_dir():
        raise LocalEvidenceReadError("Evidence directory is not a directory.")
    return resolved


def _is_within_any(path: Path, directories: tuple[Path, ...]) -> bool:
    """用路径组件判断规范化路径是否位于任一允许目录。"""
    return any(path.is_relative_to(directory) for directory in directories)


def _require_line_range(start_line: object, end_line: object) -> None:
    """引用行号必须是有限的正整数区间。"""
    if (
        isinstance(start_line, bool)
        or not isinstance(start_line, int)
        or start_line < 1
        or isinstance(end_line, bool)
        or not isinstance(end_line, int)
        or end_line < start_line
        or end_line - start_line > 10_000
    ):
        raise LocalEvidenceReadError("Evidence line range is invalid.")


def _require_positive_limit(value: object, label: str) -> None:
    """直接调用读取器时也不能关闭大小边界。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")


__all__ = [
    "LocalEvidenceExcerpt",
    "LocalEvidenceReadError",
    "read_local_markdown_evidence",
]
