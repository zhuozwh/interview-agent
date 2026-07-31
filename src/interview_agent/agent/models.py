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
    NO_EVIDENCE = "no_evidence"
    TOOL_ERROR = "tool_error"
    LLM_ERROR = "llm_error"
    INVALID_OUTPUT = "invalid_output"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """最小 Agent 输入；当前不接受调用方指定工具或文件路径。"""

    question: str


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
