"""识别并原样分离 Markdown 文件开头的 Front Matter。"""

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from interview_agent.retrieval.markdown import MarkdownDocument


class MarkdownFrontMatterError(ValueError):
    """Front Matter 以分隔符开始但缺少合法结束分隔符。"""

    def __init__(self, source_path: Path, message: str) -> None:
        super().__init__(message)
        self.source_path = source_path


def separate_front_matter(document: MarkdownDocument) -> MarkdownDocument:
    """分离文件头部 Front Matter；没有 Front Matter 时原样返回。"""
    # 已经分离过的文档直接返回，避免调用链重复增加正文行号偏移。
    if document.front_matter is not None or document.content_start_line != 1:
        return document

    lines = document.content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return document

    closing_index = _find_closing_delimiter(lines)
    if closing_index is None:
        raise MarkdownFrontMatterError(
            document.source_path,
            f"Markdown Front Matter is not closed: {document.source_path}",
        )

    # Front Matter 包含开闭分隔符并保持原始换行；正文从下一行开始。
    raw_front_matter = "".join(lines[: closing_index + 1])
    body = "".join(lines[closing_index + 1 :])
    body_start_line = closing_index + 2

    return replace(
        document,
        content=body,
        front_matter=raw_front_matter,
        content_start_line=body_start_line,
    )


def separate_front_matter_documents(
    documents: Iterable[MarkdownDocument],
) -> list[MarkdownDocument]:
    """按输入顺序分离多篇文档；任一文档错误时不静默跳过。"""
    return [separate_front_matter(document) for document in documents]


def reconstruct_document_content(document: MarkdownDocument) -> str:
    """把保留的 Front Matter 与正文重新拼回完整原文。"""
    return f"{document.front_matter or ''}{document.content}"


def _find_closing_delimiter(lines: list[str]) -> int | None:
    """从第二行开始寻找第一个独占一行的结束分隔符。"""
    for index, line in enumerate(lines[1:], start=1):
        # 同时接受 YAML 常见的 --- 和 ... 结束形式。
        if line.rstrip("\r\n") in {"---", "..."}:
            return index
    return None
