"""定义应用用例的会话结果和不含正文的追踪协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from interview_agent.agent import AgentResponse


@dataclass(frozen=True, slots=True)
class AgentTraceRecord:
    """一次应用请求的安全摘要；不保存问题、记录、证据或回答正文。"""

    trace_id: str
    session_id: str
    started_at: str
    completed_at: str
    duration_ms: int
    status: str
    intent: str | None
    route_reason: str
    tool_call_ids: tuple[str, ...]
    llm_request_id: str | None
    citation_ids: tuple[str, ...]
    error_code: str | None
    question_length: int
    interview_record_length: int


class AgentTraceStore(Protocol):
    """应用用例只依赖初始化和追踪写入能力。"""

    def initialize(self) -> None:
        """幂等初始化会话和 Agent 追踪存储。"""

    def record(self, trace: AgentTraceRecord) -> None:
        """持久化一条不含正文的执行摘要。"""


@dataclass(frozen=True, slots=True)
class AskResult:
    """FastAPI 等交互层需要的会话标识和 Agent 结果。"""

    session_id: str
    response: AgentResponse


__all__ = [
    "AgentTraceRecord",
    "AgentTraceStore",
    "AskResult",
]
