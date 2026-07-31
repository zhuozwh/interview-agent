"""提供只读、受限且可追踪的面试笔记检索 Tool。"""

from interview_agent.retrieval import (
    EmbeddingProvider,
    VectorIndexStateStore,
    VectorStore,
)
from interview_agent.tools.models import ToolTraceStore
from interview_agent.tools.scoped_search import (
    DEFAULT_MIN_SCORE,
    DEFAULT_TOP_K,
    MAX_QUERY_CHARACTERS,
    MAX_TOP_K,
    MAX_TOTAL_CHARACTERS,
    ScopedSearchError,
    ScopedSearchEvidence,
    ScopedSearchPolicy,
    ScopedSearchRequest,
    ScopedSearchResponse,
    ScopedSearchStatus,
    ScopedSemanticSearchTool,
)

SEARCH_NOTES_TOOL_NAME = "search_notes"
NOTES_SOURCE_NAMESPACE = "notes"

# 保留已经发布的 search_notes 类型名称；底层协议现由三个只读 Tool 共用。
SearchNotesStatus = ScopedSearchStatus
SearchNotesRequest = ScopedSearchRequest
SearchNotesEvidence = ScopedSearchEvidence
SearchNotesError = ScopedSearchError
SearchNotesResponse = ScopedSearchResponse


class SearchNotesTool(ScopedSemanticSearchTool):
    """只检索固定 notes 命名空间，不读取文件或生成最终回答。"""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        state_store: VectorIndexStateStore,
        trace_store: ToolTraceStore,
        min_score: float = DEFAULT_MIN_SCORE,
        max_total_characters: int = 6000,
    ) -> None:
        super().__init__(
            policy=ScopedSearchPolicy(
                tool_name=SEARCH_NOTES_TOOL_NAME,
                source_namespace=NOTES_SOURCE_NAMESPACE,
            ),
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
            min_score=min_score,
            max_total_characters=max_total_characters,
        )


__all__ = [
    "DEFAULT_MIN_SCORE",
    "DEFAULT_TOP_K",
    "MAX_QUERY_CHARACTERS",
    "MAX_TOP_K",
    "MAX_TOTAL_CHARACTERS",
    "NOTES_SOURCE_NAMESPACE",
    "SEARCH_NOTES_TOOL_NAME",
    "SearchNotesError",
    "SearchNotesEvidence",
    "SearchNotesRequest",
    "SearchNotesResponse",
    "SearchNotesStatus",
    "SearchNotesTool",
]
