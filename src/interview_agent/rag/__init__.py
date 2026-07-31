"""公开回答生成前使用的 RAG 上下文与引用契约。"""

from interview_agent.rag.context import (
    Citation,
    DEFAULT_RAG_CONTEXT_MAX_CHARACTERS,
    EvidenceBlock,
    MAX_RAG_CONTEXT_MAX_CHARACTERS,
    MIN_RAG_CONTEXT_MAX_CHARACTERS,
    RagContext,
    RagContextBudgetError,
    RagContextError,
    RagContextInputError,
    RagContextStatus,
    build_scoped_search_context,
    build_search_notes_context,
)

__all__ = [
    "Citation",
    "DEFAULT_RAG_CONTEXT_MAX_CHARACTERS",
    "EvidenceBlock",
    "MAX_RAG_CONTEXT_MAX_CHARACTERS",
    "MIN_RAG_CONTEXT_MAX_CHARACTERS",
    "RagContext",
    "RagContextBudgetError",
    "RagContextError",
    "RagContextInputError",
    "RagContextStatus",
    "build_scoped_search_context",
    "build_search_notes_context",
]
