"""把 MarkdownDocument 确定性切分为可定位到原文的片段。"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from interview_agent.retrieval.markdown import MarkdownDocument

# 当前按 Python 字符数限制片段长度，不引入模型专用 tokenizer。
DEFAULT_MAX_CHUNK_CHARACTERS = 1200

# 只识别 ATX 标题，即以 1 到 6 个 # 开始的标题；允许前置最多 3 个空格。
_HEADING_PATTERN = re.compile(r"^[ \t]{0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")

# 围栏代码块支持 Markdown 常见的反引号和波浪线写法。
_FENCE_PATTERN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")

# 每一行同时保存 1-based 行号和未经修改的原始文本。
_NumberedLine = tuple[int, str]


@dataclass(frozen=True, slots=True)
class MarkdownChunk:
    """一段 Markdown 内容及其标题和原文位置。"""

    # 下面两个字段继承自原始文档，保证片段始终能定位回来源文件。
    source_path: Path
    relative_path: Path

    # heading_path 从最高层可见父标题排列到当前标题，例如（“C++”, “并发”）。
    heading_path: tuple[str, ...]

    # 行号从 1 开始且包含首尾；同一超长行硬切后可能产生相同行号的多个片段。
    start_line: int
    end_line: int

    # chunk_index 只在当前文档内编号，从 0 开始，便于保持稳定顺序。
    chunk_index: int
    content: str


@dataclass(frozen=True, slots=True)
class _MarkdownSection:
    """切分前的内部章节；不作为 retrieval 的公共数据模型。"""

    heading_path: tuple[str, ...]
    lines: tuple[_NumberedLine, ...]


def split_markdown_document(
    document: MarkdownDocument,
    *,
    max_chunk_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS,
) -> list[MarkdownChunk]:
    """按标题和长度切分一篇文档，并保留稳定顺序与原始行号。"""
    _validate_max_chunk_characters(max_chunk_characters)

    chunks: list[MarkdownChunk] = []
    for section in _find_sections(document.content):
        for numbered_lines in _split_section_lines(
            section.lines, max_chunk_characters
        ):
            content = "".join(text for _, text in numbered_lines)

            # 纯空白文档或纯空白前言没有检索价值；标题行不属于纯空白，仍会保留。
            if not content.strip():
                continue

            chunks.append(
                MarkdownChunk(
                    source_path=document.source_path,
                    relative_path=document.relative_path,
                    heading_path=section.heading_path,
                    start_line=numbered_lines[0][0],
                    end_line=numbered_lines[-1][0],
                    chunk_index=len(chunks),
                    content=content,
                )
            )

    return chunks


def split_markdown_documents(
    documents: Iterable[MarkdownDocument],
    *,
    max_chunk_characters: int = DEFAULT_MAX_CHUNK_CHARACTERS,
) -> list[MarkdownChunk]:
    """按输入文档顺序切分多篇文档，并顺序汇总所有片段。"""
    _validate_max_chunk_characters(max_chunk_characters)

    chunks: list[MarkdownChunk] = []
    for document in documents:
        # 每篇文档独立编号；扁平结果仍保持加载器提供的稳定文档顺序。
        chunks.extend(
            split_markdown_document(
                document,
                max_chunk_characters=max_chunk_characters,
            )
        )
    return chunks


def _find_sections(content: str) -> list[_MarkdownSection]:
    """识别代码围栏外的 ATX 标题，并按标题边界生成章节。"""
    numbered_lines = tuple(enumerate(content.splitlines(keepends=True), start=1))
    if not numbered_lines:
        return []

    sections: list[_MarkdownSection] = []
    heading_stack: list[tuple[int, str]] = []
    current_heading_path: tuple[str, ...] = ()
    current_lines: list[_NumberedLine] = []
    open_fence: tuple[str, int] | None = None

    for line_number, line in numbered_lines:
        fence = _match_fence(line)

        if open_fence is not None:
            # 围栏内部所有内容都属于正文，包括看起来像标题的行。
            current_lines.append((line_number, line))
            if _closes_fence(fence, open_fence):
                open_fence = None
            continue

        if fence is not None:
            # 当前行开启代码围栏；围栏标记本身也保留在片段正文中。
            open_fence = (fence[0][0], len(fence[0]))
            current_lines.append((line_number, line))
            continue

        heading = _match_heading(line)
        if heading is None:
            current_lines.append((line_number, line))
            continue

        # 新标题开始前，先把前言或上一章节完整保存。
        if current_lines:
            sections.append(
                _MarkdownSection(
                    heading_path=current_heading_path,
                    lines=tuple(current_lines),
                )
            )

        level, title = heading
        # 同级或更深层旧标题已经结束；跨级标题不补造不存在的中间层。
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        heading_stack.append((level, title))

        current_heading_path = tuple(title for _, title in heading_stack)
        current_lines = [(line_number, line)]

    if current_lines:
        sections.append(
            _MarkdownSection(
                heading_path=current_heading_path,
                lines=tuple(current_lines),
            )
        )

    return sections


def _match_heading(line: str) -> tuple[int, str] | None:
    """返回标题级别和清理后的标题文本；普通行返回 None。"""
    # 去掉换行符仅用于语法判断，真正存入 chunk 的仍是原始 line。
    line_without_ending = line.rstrip("\r\n")
    match = _HEADING_PATTERN.fullmatch(line_without_ending)
    if match is None:
        return None

    hashes, raw_title = match.groups()
    title = raw_title or ""

    # Markdown 允许标题末尾使用空格分隔的 # 作为关闭标记，不属于标题文字。
    title = re.sub(r"[ \t]+#+[ \t]*$", "", title).strip()
    return len(hashes), title


def _match_fence(line: str) -> tuple[str, str] | None:
    """返回围栏标记和后缀；非围栏行返回 None。"""
    line_without_ending = line.rstrip("\r\n")
    match = _FENCE_PATTERN.fullmatch(line_without_ending)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _closes_fence(
    fence: tuple[str, str] | None, open_fence: tuple[str, int]
) -> bool:
    """判断当前围栏是否能关闭已打开的代码块。"""
    if fence is None:
        return False

    marker, suffix = fence
    open_character, open_length = open_fence
    return (
        marker[0] == open_character
        and len(marker) >= open_length
        and not suffix.strip()
    )


def _split_section_lines(
    lines: tuple[_NumberedLine, ...], max_characters: int
) -> Iterator[tuple[_NumberedLine, ...]]:
    """短段落尽量保持完整，超长段落再按行和字符逐级切分。"""
    current_lines: list[_NumberedLine] = []
    current_size = 0

    for paragraph in _paragraph_blocks(lines):
        paragraph_size = sum(len(text) for _, text in paragraph)

        if paragraph_size <= max_characters:
            # 当前片段放不下整个短段落时，先结束旧片段，避免从段落中间切开。
            if current_lines and current_size + paragraph_size > max_characters:
                yield tuple(current_lines)
                current_lines = []
                current_size = 0

            current_lines.extend(paragraph)
            current_size += paragraph_size
            continue

        # 超长段落开始前先结束已有短段落，然后逐行处理该段落。
        if current_lines:
            yield tuple(current_lines)
            current_lines = []
            current_size = 0

        for line_number, text in paragraph:
            for fragment in _split_long_line(line_number, text, max_characters):
                fragment_size = len(fragment[1])
                if current_lines and current_size + fragment_size > max_characters:
                    yield tuple(current_lines)
                    current_lines = []
                    current_size = 0

                current_lines.append(fragment)
                current_size += fragment_size

                if current_size == max_characters:
                    yield tuple(current_lines)
                    current_lines = []
                    current_size = 0

        # 超长段落的尾部不与下一个段落合并，使段落边界保持清晰。
        if current_lines:
            yield tuple(current_lines)
            current_lines = []
            current_size = 0

    if current_lines:
        yield tuple(current_lines)


def _paragraph_blocks(
    lines: tuple[_NumberedLine, ...],
) -> Iterator[tuple[_NumberedLine, ...]]:
    """按空白行形成段落块，并把空白行原样保留在前一个块中。"""
    current: list[_NumberedLine] = []
    for numbered_line in lines:
        current.append(numbered_line)
        if not numbered_line[1].strip():
            yield tuple(current)
            current = []

    if current:
        yield tuple(current)


def _split_long_line(
    line_number: int, text: str, max_characters: int
) -> Iterator[_NumberedLine]:
    """仅当单行仍然过长时按字符硬切，所有片段保留同一原始行号。"""
    for start in range(0, len(text), max_characters):
        yield line_number, text[start : start + max_characters]


def _validate_max_chunk_characters(value: int) -> None:
    """拒绝会让切分行为失去意义的非正数上限。"""
    if value <= 0:
        raise ValueError("max_chunk_characters must be greater than zero")
