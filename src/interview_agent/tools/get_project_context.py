"""提供只读、受限且可追踪的项目资料检索 Tool。"""

from interview_agent.retrieval import (
    EmbeddingProvider,
    VectorIndexStateStore,
    VectorStore,
)
from interview_agent.tools.models import ToolTraceStore
from interview_agent.tools.scoped_search import (
    DEFAULT_MIN_SCORE,
    ScopedSearchError,
    ScopedSearchEvidence,
    ScopedSearchPolicy,
    ScopedSearchRequest,
    ScopedSearchResponse,
    ScopedSearchStatus,
    ScopedSemanticSearchTool,
)

GET_PROJECT_CONTEXT_TOOL_NAME = "get_project_context"
PROJECT_SOURCE_NAMESPACE = "projects"

GetProjectContextStatus = ScopedSearchStatus
GetProjectContextRequest = ScopedSearchRequest
GetProjectContextEvidence = ScopedSearchEvidence
GetProjectContextError = ScopedSearchError
GetProjectContextResponse = ScopedSearchResponse


class GetProjectContextTool(ScopedSemanticSearchTool):
    """只检索项目说明、设计和实现状态，不读取源码或 Git 历史。"""

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
                tool_name=GET_PROJECT_CONTEXT_TOOL_NAME,
                source_namespace=PROJECT_SOURCE_NAMESPACE,
            ),
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
            min_score=min_score,
            max_total_characters=max_total_characters,
        )


__all__ = [
    "GET_PROJECT_CONTEXT_TOOL_NAME",
    "PROJECT_SOURCE_NAMESPACE",
    "GetProjectContextError",
    "GetProjectContextEvidence",
    "GetProjectContextRequest",
    "GetProjectContextResponse",
    "GetProjectContextStatus",
    "GetProjectContextTool",
]
