"""用可解释规则实现第一阶段的轻量意图路由。"""

from __future__ import annotations

from interview_agent.agent.models import AgentIntent, AgentRoute

_REVIEW_MARKERS = (
    "面试复盘",
    "面试记录",
    "面试表现",
    "面试官问",
    "复盘这场面试",
)
_RESUME_MARKERS = (
    "我的简历",
    "简历里",
    "我的经历",
    "我做过",
    "我的实习",
    "个人经历",
    "resume",
)
_PROJECT_MARKERS = (
    "我的项目",
    "项目中",
    "项目里",
    "这个项目",
    "服务框架",
    "当前实现",
    "实现状态",
    "interview-agent",
    "interview agent",
)


def route_question(question: str) -> AgentRoute:
    """优先识别个人资料任务，其余问题使用面试笔记检索。"""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    normalized = question.casefold()
    if any(marker in normalized for marker in _REVIEW_MARKERS):
        return AgentRoute(
            intent=AgentIntent.INTERVIEW_REVIEW,
            tool_name=None,
            reason_code="interview_review_tool_unavailable",
        )
    if any(marker in normalized for marker in _RESUME_MARKERS):
        return AgentRoute(
            intent=AgentIntent.RESUME_CONTEXT,
            tool_name=None,
            reason_code="resume_tool_unavailable",
        )
    if any(marker in normalized for marker in _PROJECT_MARKERS):
        return AgentRoute(
            intent=AgentIntent.PROJECT_CONTEXT,
            tool_name=None,
            reason_code="project_tool_unavailable",
        )
    return AgentRoute(
        intent=AgentIntent.KNOWLEDGE_QUESTION,
        tool_name="search_notes",
        reason_code="knowledge_question_requires_notes",
    )
