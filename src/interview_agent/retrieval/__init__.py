"""公开 Markdown 读取、切分和增量索引准备能力。"""

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
from interview_agent.retrieval.front_matter import (
    MarkdownFrontMatterError,
    reconstruct_document_content,
    separate_front_matter,
    separate_front_matter_documents,
)
from interview_agent.retrieval.indexing import (
    IndexChunk,
    IndexDocument,
    IndexPlan,
    StoredChunkState,
    StoredDocumentState,
    build_index_plan,
    prepare_index_document,
    prepare_index_documents,
)
from interview_agent.retrieval.chunking import (
    MarkdownChunk,
    split_markdown_document,
    split_markdown_documents,
)

# __all__ 明确声明该模块对外承诺的公共接口，以下划线开头的辅助函数不会暴露。
__all__ = [
    "IndexChunk",
    "IndexDocument",
    "IndexPlan",
    "MarkdownDiscoveryError",
    "MarkdownChunk",
    "MarkdownDocument",
    "MarkdownFrontMatterError",
    "MarkdownLoadError",
    "MarkdownPathError",
    "MarkdownReadError",
    "MarkdownSizeError",
    "StoredChunkState",
    "StoredDocumentState",
    "build_index_plan",
    "load_markdown_documents",
    "prepare_index_document",
    "prepare_index_documents",
    "reconstruct_document_content",
    "separate_front_matter",
    "separate_front_matter_documents",
    "split_markdown_document",
    "split_markdown_documents",
]
