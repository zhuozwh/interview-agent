"""实现一次路由、一次只读检索、一次回答生成的最小 Agent 闭环。"""

from __future__ import annotations

import re
from typing import Protocol
from uuid import UUID, uuid4

from interview_agent.agent.models import (
    AgentError,
    AgentIntent,
    AgentRequest,
    AgentResponse,
    AgentStatus,
)
from interview_agent.agent.prompts import build_knowledge_answer_messages
from interview_agent.agent.router import route_question
from interview_agent.llm import LLMClient, LLMError, LLMResponse
from interview_agent.rag import (
    Citation,
    RagContextError,
    RagContextStatus,
    build_search_notes_context,
)
from interview_agent.tools import (
    SearchNotesRequest,
    SearchNotesResponse,
    SearchNotesStatus,
)

MAX_AGENT_QUESTION_CHARACTERS = 480
DEFAULT_AGENT_TOP_K = 5
MAX_AGENT_ANSWER_CHARACTERS = 8_000

_EXACT_CITATION_PATTERN = re.compile(r"\[S([1-9][0-9]*)\]")
_CITATION_START_PATTERN = re.compile(r"\[S")
_UNSUPPORTED_LINK_PATTERN = re.compile(r"(?:https?|file)://", re.IGNORECASE)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]\r\n]{1,200}\]\([^\)\r\n]{1,500}\)")
_WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s]+"
)
_SAFE_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class SearchNotesExecutor(Protocol):
    """Agent 只依赖 search_notes 的稳定执行边界。"""

    def execute(
        self,
        request: SearchNotesRequest,
        *,
        trace_id: str | None = None,
    ) -> SearchNotesResponse:
        """执行一次只读检索。"""


class KnowledgeAgent:
    """当前只支持知识问答，遇到其他意图会明确停止。"""

    def __init__(
        self,
        *,
        search_notes: SearchNotesExecutor,
        llm_client: LLMClient,
        context_max_characters: int = 8_000,
        top_k: int = DEFAULT_AGENT_TOP_K,
        max_answer_characters: int = MAX_AGENT_ANSWER_CHARACTERS,
    ) -> None:
        if (
            isinstance(context_max_characters, bool)
            or not isinstance(context_max_characters, int)
            or not 512 <= context_max_characters <= 50_000
        ):
            raise ValueError(
                "context_max_characters must be between 512 and 50000"
            )
        if (
            isinstance(top_k, bool)
            or not isinstance(top_k, int)
            or not 1 <= top_k <= 10
        ):
            raise ValueError("top_k must be between 1 and 10")
        if (
            isinstance(max_answer_characters, bool)
            or not isinstance(max_answer_characters, int)
            or not 1 <= max_answer_characters <= 20_000
        ):
            raise ValueError(
                "max_answer_characters must be between 1 and 20000"
            )
        self.search_notes = search_notes
        self.llm_client = llm_client
        self.context_max_characters = context_max_characters
        self.top_k = top_k
        self.max_answer_characters = max_answer_characters

    def execute(
        self,
        request: AgentRequest,
        *,
        trace_id: str | None = None,
    ) -> AgentResponse:
        """执行单工具闭环，并在任何失败分支停止扩展调用。"""
        response_trace_id, trace_error = _normalize_trace_id(trace_id)
        if trace_error is not None:
            return _error_response(
                trace_id=response_trace_id,
                status=AgentStatus.INVALID_INPUT,
                code="invalid_trace_id",
                message="trace_id must be a canonical UUID string.",
            )

        validation_error = _validate_request(request)
        if validation_error is not None:
            return _error_response(
                trace_id=response_trace_id,
                status=AgentStatus.INVALID_INPUT,
                code=validation_error.code,
                message=validation_error.message,
            )
        question = request.question.strip()
        route = route_question(question)
        if route.intent is not AgentIntent.KNOWLEDGE_QUESTION:
            return AgentResponse(
                trace_id=response_trace_id,
                status=AgentStatus.UNSUPPORTED,
                intent=route.intent,
                route_reason=route.reason_code,
                answer=(
                    "当前单工具 Agent 尚未接入该资料类型，"
                    "因此没有调用 LLM，也不会根据通用知识猜测个人事实。"
                ),
                citations=(),
                tool_call_ids=(),
                llm_request_id=None,
                error=None,
            )

        try:
            tool_response = self.search_notes.execute(
                SearchNotesRequest(query=question, top_k=self.top_k),
                trace_id=response_trace_id,
            )
        except Exception:
            return _error_response(
                trace_id=response_trace_id,
                status=AgentStatus.INTERNAL_ERROR,
                intent=route.intent,
                route_reason=route.reason_code,
                code="unexpected_tool_failure",
                message="The retrieval Tool failed outside its stable protocol.",
            )
        if not isinstance(tool_response, SearchNotesResponse):
            return _error_response(
                trace_id=response_trace_id,
                status=AgentStatus.INTERNAL_ERROR,
                intent=route.intent,
                route_reason=route.reason_code,
                code="invalid_tool_response",
                message="The retrieval Tool returned an invalid response.",
            )
        tool_call_id = _canonical_uuid(tool_response.tool_call_id)
        if tool_call_id is None:
            return _error_response(
                trace_id=response_trace_id,
                status=AgentStatus.INTERNAL_ERROR,
                intent=route.intent,
                route_reason=route.reason_code,
                code="invalid_tool_response",
                message="The retrieval Tool returned an invalid identity.",
            )
        tool_call_ids = (tool_call_id,)
        if tool_response.trace_id != response_trace_id:
            return _error_response(
                trace_id=response_trace_id,
                status=AgentStatus.INTERNAL_ERROR,
                intent=route.intent,
                route_reason=route.reason_code,
                tool_call_ids=tool_call_ids,
                code="tool_trace_mismatch",
                message="The Tool response could not be linked to this request.",
            )

        try:
            context = build_search_notes_context(
                tool_response,
                max_characters=self.context_max_characters,
            )
        except RagContextError:
            return _error_response(
                trace_id=response_trace_id,
                status=AgentStatus.INTERNAL_ERROR,
                intent=route.intent,
                route_reason=route.reason_code,
                tool_call_ids=tool_call_ids,
                code="context_build_failed",
                message="The retrieved evidence could not be validated.",
            )

        if context.status is RagContextStatus.NO_EVIDENCE:
            return AgentResponse(
                trace_id=response_trace_id,
                status=AgentStatus.NO_EVIDENCE,
                intent=route.intent,
                route_reason=route.reason_code,
                answer=(
                    "当前知识库没有找到足够可靠的证据，"
                    "因此没有调用 LLM 生成确定答案。"
                ),
                citations=(),
                tool_call_ids=tool_call_ids,
                llm_request_id=None,
                error=None,
            )
        if context.status is RagContextStatus.TOOL_ERROR:
            return _error_response(
                trace_id=response_trace_id,
                status=AgentStatus.TOOL_ERROR,
                intent=route.intent,
                route_reason=route.reason_code,
                tool_call_ids=tool_call_ids,
                code=_safe_error_code(context.error_code, "tool_failed"),
                message="The knowledge retrieval Tool failed.",
                retryable=_tool_error_retryable(tool_response),
            )

        messages = build_knowledge_answer_messages(question, context)
        try:
            llm_response = self.llm_client.complete(messages)
        except LLMError as error:
            return _error_response(
                trace_id=response_trace_id,
                status=AgentStatus.LLM_ERROR,
                intent=route.intent,
                route_reason=route.reason_code,
                tool_call_ids=tool_call_ids,
                code=_safe_error_code(error.code, "llm_error"),
                message="The answer model could not complete the request.",
                retryable=error.retryable,
            )
        except Exception:
            return _error_response(
                trace_id=response_trace_id,
                status=AgentStatus.INTERNAL_ERROR,
                intent=route.intent,
                route_reason=route.reason_code,
                tool_call_ids=tool_call_ids,
                code="unexpected_llm_failure",
                message="The answer generation failed unexpectedly.",
            )

        if not isinstance(llm_response, LLMResponse):
            return _error_response(
                trace_id=response_trace_id,
                status=AgentStatus.INVALID_OUTPUT,
                intent=route.intent,
                route_reason=route.reason_code,
                tool_call_ids=tool_call_ids,
                code="invalid_llm_response",
                message="The answer model returned an invalid response contract.",
            )
        llm_request_id = _safe_llm_request_id(llm_response.request_id)
        if llm_request_id is None:
            return _error_response(
                trace_id=response_trace_id,
                status=AgentStatus.INVALID_OUTPUT,
                intent=route.intent,
                route_reason=route.reason_code,
                tool_call_ids=tool_call_ids,
                code="invalid_llm_response",
                message="The answer model returned an invalid request identity.",
            )
        validation = _validate_answer(
            llm_response.content,
            llm_response.finish_reason,
            context.citations,
            max_characters=self.max_answer_characters,
        )
        if isinstance(validation, AgentError):
            return AgentResponse(
                trace_id=response_trace_id,
                status=AgentStatus.INVALID_OUTPUT,
                intent=route.intent,
                route_reason=route.reason_code,
                answer=None,
                citations=(),
                tool_call_ids=tool_call_ids,
                llm_request_id=llm_request_id,
                error=validation,
            )
        return AgentResponse(
            trace_id=response_trace_id,
            status=AgentStatus.SUCCESS,
            intent=route.intent,
            route_reason=route.reason_code,
            answer=llm_response.content,
            citations=validation,
            tool_call_ids=tool_call_ids,
            llm_request_id=llm_request_id,
            error=None,
        )


def _validate_request(request: object) -> AgentError | None:
    """问题在路由和 Tool 调用前完成本地校验。"""
    if not isinstance(request, AgentRequest):
        return AgentError(
            code="invalid_request",
            message="request must be an AgentRequest.",
            retryable=False,
        )
    if (
        not isinstance(request.question, str)
        or not request.question.strip()
        or "\0" in request.question
        or not _is_valid_utf8(request.question)
    ):
        return AgentError(
            code="invalid_question",
            message="question must be non-empty valid UTF-8 text without NUL.",
            retryable=False,
        )
    if len(request.question.strip()) > MAX_AGENT_QUESTION_CHARACTERS:
        return AgentError(
            code="question_too_long",
            message=(
                "question must not exceed "
                f"{MAX_AGENT_QUESTION_CHARACTERS} characters."
            ),
            retryable=False,
        )
    return None


def _validate_answer(
    answer: str,
    finish_reason: str,
    citations: tuple[Citation, ...],
    *,
    max_characters: int,
) -> tuple[Citation, ...] | AgentError:
    """拒绝截断回答、伪造引用和可伪装成外部链接的引用语法。"""
    if finish_reason != "stop":
        return AgentError(
            code="incomplete_llm_output",
            message="The answer model did not finish normally.",
            retryable=True,
        )
    if (
        not isinstance(answer, str)
        or not answer.strip()
        or "\0" in answer
        or not _is_valid_utf8(answer)
        or len(answer) > max_characters
    ):
        return AgentError(
            code="invalid_answer_content",
            message="The answer content is empty, invalid, or too long.",
            retryable=False,
        )
    matches = tuple(_EXACT_CITATION_PATTERN.finditer(answer))
    valid_starts = {match.start() for match in matches}
    if any(
        match.start() not in valid_starts
        for match in _CITATION_START_PATTERN.finditer(answer)
    ):
        return AgentError(
            code="malformed_citation",
            message="The answer contains malformed citation syntax.",
            retryable=False,
        )
    if not matches:
        return AgentError(
            code="missing_citation",
            message="The evidence-based answer contains no citation.",
            retryable=False,
        )

    by_id = {citation.citation_id: citation for citation in citations}
    selected: list[Citation] = []
    seen: set[str] = set()
    for match in matches:
        citation_id = f"S{match.group(1)}"
        citation = by_id.get(citation_id)
        if citation is None:
            return AgentError(
                code="unknown_citation",
                message="The answer cites evidence that was not retrieved.",
                retryable=False,
            )
        suffix = answer[match.end() :].lstrip()
        if suffix.startswith(("(", ":", "（", "：")):
            return AgentError(
                code="unsafe_citation_format",
                message="The answer must not attach links or definitions to citations.",
                retryable=False,
            )
        if citation_id not in seen:
            seen.add(citation_id)
            selected.append(citation)
    if (
        _UNSUPPORTED_LINK_PATTERN.search(answer)
        or _MARKDOWN_LINK_PATTERN.search(answer)
        or _WINDOWS_ABSOLUTE_PATH_PATTERN.search(answer)
    ):
        return AgentError(
            code="unsafe_answer_link",
            message=(
                "The answer must not invent links or expose absolute paths."
            ),
            retryable=False,
        )
    return tuple(selected)


def _normalize_trace_id(value: object) -> tuple[str, AgentError | None]:
    """无调用方 ID 时生成 UUID；非法 ID 不进入 Tool 追踪。"""
    generated = str(uuid4())
    if value is None:
        return generated, None
    if not isinstance(value, str):
        return generated, AgentError(
            code="invalid_trace_id",
            message="trace_id must be a canonical UUID string.",
            retryable=False,
        )
    try:
        normalized = str(UUID(value))
    except ValueError:
        return generated, AgentError(
            code="invalid_trace_id",
            message="trace_id must be a canonical UUID string.",
            retryable=False,
        )
    if normalized != value:
        return generated, AgentError(
            code="invalid_trace_id",
            message="trace_id must be a canonical UUID string.",
            retryable=False,
        )
    return normalized, None


def _canonical_uuid(value: object) -> str | None:
    """Tool 身份必须是规范 UUID，异常值不能进入应用响应。"""
    if not isinstance(value, str):
        return None
    try:
        normalized = str(UUID(value))
    except ValueError:
        return None
    return normalized if normalized == value else None


def _safe_error_code(value: object, fallback: str) -> str:
    """错误码可能进入日志和 API，只允许固定的小写标识格式。"""
    if isinstance(value, str) and _SAFE_ERROR_CODE_PATTERN.fullmatch(value):
        return value
    return fallback


def _safe_llm_request_id(value: object) -> str | None:
    """供应方请求 ID 只能是有界单行 UTF-8 文本。"""
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or not _is_valid_utf8(value)
    ):
        return None
    return value


def _tool_error_retryable(response: SearchNotesResponse) -> bool:
    """只使用 Tool 已经归一化的重试标记。"""
    return response.error.retryable if response.error is not None else False


def _error_response(
    *,
    trace_id: str,
    status: AgentStatus,
    code: str,
    message: str,
    intent: AgentIntent | None = None,
    route_reason: str = "request_validation_failed",
    tool_call_ids: tuple[str, ...] = (),
    retryable: bool = False,
) -> AgentResponse:
    """集中构造不暴露正文和第三方细节的错误响应。"""
    return AgentResponse(
        trace_id=trace_id,
        status=status,
        intent=intent,
        route_reason=route_reason,
        answer=None,
        citations=(),
        tool_call_ids=tool_call_ids,
        llm_request_id=None,
        error=AgentError(
            code=code,
            message=message,
            retryable=retryable,
        ),
    )


def _is_valid_utf8(value: str) -> bool:
    """拒绝孤立代理字符，保证提示词 JSON 可编码。"""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
