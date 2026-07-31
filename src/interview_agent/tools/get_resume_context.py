"""提供最小暴露、只读且可追踪的简历资料检索 Tool。"""

import re

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

_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])",
    re.UNICODE,
)
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d(?:[- ]?\d){8}(?!\d)"
)
_IDENTITY_CARD_PATTERN = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_MESSAGING_ID_PATTERN = re.compile(
    r"(?i)(?P<label>微信|wechat|wx)\s*[:：]\s*[A-Za-z0-9_-]{5,64}"
)

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
                content_transform=_redact_resume_sensitive_data,
            ),
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
            min_score=min_score,
            max_total_characters=max_total_characters,
        )


def _redact_resume_sensitive_data(content: str) -> str:
    """在正文离开 Tool 前移除常见联系方式和中国身份证号。"""
    redacted = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", content)
    redacted = _PHONE_PATTERN.sub("[REDACTED_PHONE]", redacted)
    redacted = _IDENTITY_CARD_PATTERN.sub("[REDACTED_ID]", redacted)
    return _MESSAGING_ID_PATTERN.sub(
        lambda match: f"{match.group('label')}：[REDACTED_ACCOUNT]",
        redacted,
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
