"""用可解释规则实现第一阶段的轻量意图路由。"""

from __future__ import annotations

from interview_agent.agent.models import AgentIntent, AgentRoute
from interview_agent.core.query_policy import infer_query_namespace

_REVIEW_MARKERS = (
    "面试复盘",
    "面试记录",
    "面试表现",
    "面试官问",
    "复盘这场面试",
)
def route_question(
    question: str,
    *,
    has_interview_record: bool = False,
) -> AgentRoute:
    """优先识别个人资料任务，其余问题使用面试笔记检索。"""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    if not isinstance(has_interview_record, bool):
        raise ValueError("has_interview_record must be a boolean")
    normalized = question.casefold()
    if has_interview_record:
        return AgentRoute(
            intent=AgentIntent.INTERVIEW_REVIEW,
            tool_name=None,
            reason_code="provided_interview_record_requires_review",
        )
    if any(marker in normalized for marker in _REVIEW_MARKERS):
        return AgentRoute(
            intent=AgentIntent.INTERVIEW_REVIEW,
            tool_name=None,
            reason_code="interview_review_requires_record",
        )
    namespace = infer_query_namespace(normalized)
    if namespace == "resume":
        return AgentRoute(
            intent=AgentIntent.RESUME_CONTEXT,
            tool_name="get_resume_context",
            reason_code="resume_question_requires_resume_context",
        )
    if namespace == "projects":
        return AgentRoute(
            intent=AgentIntent.PROJECT_CONTEXT,
            tool_name="get_project_context",
            reason_code="project_question_requires_project_context",
        )
    return AgentRoute(
        intent=AgentIntent.KNOWLEDGE_QUESTION,
        tool_name="search_notes",
        reason_code="knowledge_question_requires_notes",
    )
