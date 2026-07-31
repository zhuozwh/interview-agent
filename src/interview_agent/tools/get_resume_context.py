"""提供最小暴露、只读且可追踪的简历资料检索 Tool。"""

from interview_agent.core.privacy import redact_common_personal_data
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

GET_RESUME_CONTEXT_TOOL_NAME = "get_resume_context"
RESUME_SOURCE_NAMESPACE = "resume"

GetResumeContextStatus = ScopedSearchStatus
GetResumeContextRequest = ScopedSearchRequest
GetResumeContextEvidence = ScopedSearchEvidence
GetResumeContextError = ScopedSearchError
GetResumeContextResponse = ScopedSearchResponse


class GetResumeContextTool(ScopedSemanticSearchTool):
    """只返回问题所需的简历片段，并先脱敏常见联系方式和证件号。"""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        state_store: VectorIndexStateStore,
        trace_store: ToolTraceStore,
        min_score: float = DEFAULT_MIN_SCORE,
        max_total_characters: int = 3000,
    ) -> None:
        super().__init__(
            policy=ScopedSearchPolicy(
                tool_name=GET_RESUME_CONTEXT_TOOL_NAME,
                source_namespace=RESUME_SOURCE_NAMESPACE,
                content_transform=redact_common_personal_data,
            ),
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
            min_score=min_score,
            max_total_characters=max_total_characters,
        )
__all__ = [
    "GET_RESUME_CONTEXT_TOOL_NAME",
    "RESUME_SOURCE_NAMESPACE",
    "GetResumeContextError",
    "GetResumeContextEvidence",
    "GetResumeContextRequest",
    "GetResumeContextResponse",
    "GetResumeContextStatus",
    "GetResumeContextTool",
]
