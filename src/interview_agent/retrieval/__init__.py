"""公开 Markdown 只读加载能力。"""

# 调用方只需从 retrieval 包导入这些名称，不必了解内部文件如何组织。
from interview_agent.retrieval.markdown import (
    MarkdownDiscoveryError,
    MarkdownDocument,
    MarkdownLoadError,
    MarkdownPathError,
    MarkdownReadError,
    MarkdownSizeError,
    load_markdown_documents,
)

# __all__ 明确声明该模块对外承诺的公共接口，以下划线开头的辅助函数不会暴露。
__all__ = [
    "MarkdownDiscoveryError",
    "MarkdownDocument",
    "MarkdownLoadError",
    "MarkdownPathError",
    "MarkdownReadError",
    "MarkdownSizeError",
    "load_markdown_documents",
]
