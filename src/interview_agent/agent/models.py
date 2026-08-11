"""定义单 Agent 执行循环对应用层公开的稳定模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from interview_agent.rag import Citation


class AgentIntent(StrEnum):
    """Phase 1 需要识别的四类用户任务。"""

    KNOWLEDGE_QUESTION = "knowledge_question"
    PROJECT_CONTEXT = "project_context"
    RESUME_CONTEXT = "resume_context"
    INTERVIEW_REVIEW = "interview_review"


class AgentStatus(StrEnum):
    """应用层可稳定映射的 Agent 结果状态。"""

    SUCCESS = "success"
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED = "unsupported"
    POLICY_REFUSED = "policy_refused"
    NO_EVIDENCE = "no_evidence"
    TOOL_ERROR = "tool_error"
    LLM_ERROR = "llm_error"
    INVALID_OUTPUT = "invalid_output"
    INTERNAL_ERROR = "internal_error"


class AgentConfidence(StrEnum):
    """向交互层解释回答证据强度，而不是模型主观概率。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """最小 Agent 输入；调用方不能指定工具、namespace 或文件路径。"""

    question: str
    interview_record: str | None = None
    previous_question: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRoute:
    """可解释的确定性路由结果。"""

    intent: AgentIntent
    tool_name: str | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class AgentError:
    """不包含问题、证据正文或供应方响应的稳定错误。"""

    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class AgentResponse:
    """一次受限执行的最终结果和可核验来源。"""

    trace_id: str
    status: AgentStatus
    intent: AgentIntent | None
    route_reason: str
    answer: str | None
    citations: tuple[Citation, ...]
    tool_call_ids: tuple[str, ...]
    llm_request_id: str | None
    error: AgentError | None
    confidence: AgentConfidence | None = None
    follow_up_questions: tuple[str, ...] = ()
