"""把 Agent 失败转换为可公开返回和持久化的稳定字段。"""

from __future__ import annotations

import re

from interview_agent.agent import AgentError, AgentStatus

_SAFE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_PUBLIC_MESSAGES = {
    "incomplete_llm_output": "The answer model did not finish normally.",
    "llm_connection_failed": "The answer model could not be reached.",
    "llm_timeout": "The answer model timed out.",
    "llm_authentication_failed": "The answer model rejected authentication.",
    "llm_rate_limited": "The answer model is temporarily rate limited.",
    "llm_request_rejected": "The answer model rejected the request.",
    "llm_service_unavailable": "The answer model is temporarily unavailable.",
    "llm_invalid_response": "The answer model returned an invalid response.",
    "invalid_llm_response": "The answer model returned an invalid response.",
    "service_unavailable": "The local Agent is not ready.",
    "local_request_failed": "The local request could not be completed.",
}


def public_error_fields(
    error: AgentError | None,
    status: AgentStatus,
) -> tuple[str | None, str | None]:
    """只保留安全错误码，并用固定文案替换任意底层异常正文。"""
    if error is None:
        return None, None
    raw_code = (
        error.code.strip().lower()
        if isinstance(error.code, str)
        else ""
    )
    code = raw_code if _SAFE_ERROR_CODE.fullmatch(raw_code) else "agent_error"
    message = _PUBLIC_MESSAGES.get(
        code,
        _status_message(status),
    )
    return code, message


def _status_message(status: AgentStatus) -> str:
    """未知错误码只按公开领域状态给出最小固定描述。"""
    if status is AgentStatus.INVALID_INPUT:
        return "The request input is invalid."
    if status is AgentStatus.UNSUPPORTED:
        return "The request is not supported."
    if status is AgentStatus.TOOL_ERROR:
        return "The local retrieval tool could not complete the request."
    if status is AgentStatus.LLM_ERROR:
        return "The answer model could not complete the request."
    if status is AgentStatus.INVALID_OUTPUT:
        return "The answer model returned an invalid response."
    return "The Agent could not complete the request."


__all__ = ["public_error_fields"]
