"""验证三 Tool Agent 路由、复盘和跨 namespace 防御边界。"""

import json
from dataclasses import replace

import pytest

from interview_agent.agent import (
    AgentConfidence,
    AgentIntent,
    AgentRequest,
    AgentStatus,
    KnowledgeAgent,
    MAX_INTERVIEW_RECORD_CHARACTERS,
)
from interview_agent.llm import LLMResponse, LLMUsage
from interview_agent.tools.scoped_search import (
    ScopedSearchEvidence,
    ScopedSearchRequest,
    ScopedSearchResponse,
    ScopedSearchStatus,
)

_TRACE_ID = "11111111-1111-4111-8111-111111111111"
_TOOL_CALL_ID = "22222222-2222-4222-8222-222222222222"
_FINGERPRINT = "a" * 64


class FakeScopedTool:
    """记录 Agent 实际选择，并返回固定协议响应。"""

    def __init__(self, response: ScopedSearchResponse) -> None:
        self.response = response
        self.calls: list[tuple[ScopedSearchRequest, str | None]] = []

    def execute(
        self,
        request: ScopedSearchRequest,
        *,
        trace_id: str | None = None,
    ) -> ScopedSearchResponse:
        self.calls.append((request, trace_id))
        return replace(self.response, trace_id=trace_id)


class FakeLLM:
    """保存提示词，并返回无需网络的确定性回答。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        return LLMResponse(
            request_id="provider-request-1",
            model="test-model",
            content=self.content,
            finish_reason="stop",
            system_fingerprint=None,
            usage=LLMUsage(
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
            ),
        )


def _tool_response(
    tool_name: str,
    namespace: str,
    *,
    content: str,
) -> ScopedSearchResponse:
    """构造一条可定位的固定 namespace 证据。"""
    return ScopedSearchResponse(
        tool_name=tool_name,
        tool_call_id=_TOOL_CALL_ID,
        trace_id=_TRACE_ID,
        status=ScopedSearchStatus.SUCCESS,
        results=(
            ScopedSearchEvidence(
                rank=1,
                chunk_id=f"{namespace}-chunk",
                document_id=f"{namespace}-document",
                source_type="markdown",
                source_namespace=namespace,
                relative_path=f"{namespace}.md",
                heading_path=("说明",),
                start_line=1,
                end_line=3,
                fingerprint=_FINGERPRINT,
                content=content,
                content_truncated=False,
                score=0.91,
            ),
        ),
        error=None,
        duration_ms=2,
    )


@pytest.mark.parametrize(
    (
        "question",
        "intent",
        "selected_tool",
        "namespace",
        "content",
    ),
    [
        (
            "我的项目当前实现状态是什么？",
            AgentIntent.PROJECT_CONTEXT,
            "get_project_context",
            "projects",
            "当前实现了单 Reactor 事件循环。",
        ),
        (
            "我的简历里有哪些后端经历？",
            AgentIntent.RESUME_CONTEXT,
            "get_resume_context",
            "resume",
            "后端实习期间实现了服务模块。",
        ),
    ],
)
def test_agent_selects_exactly_one_scoped_tool(
    question: str,
    intent: AgentIntent,
    selected_tool: str,
    namespace: str,
    content: str,
) -> None:
    """项目和简历问题不能落入 notes，也不能同时调用多个 Tool。"""
    notes = FakeScopedTool(
        _tool_response("search_notes", "notes", content="通用笔记")
    )
    project = FakeScopedTool(
        _tool_response(
            "get_project_context",
            "projects",
            content="当前实现了单 Reactor 事件循环。",
        )
    )
    resume = FakeScopedTool(
        _tool_response(
            "get_resume_context",
            "resume",
            content="后端实习期间实现了服务模块。",
        )
    )
    llm = FakeLLM("资料显示的结论如下。[S1]")
    agent = KnowledgeAgent(
        search_notes=notes,
        get_project_context=project,
        get_resume_context=resume,
        llm_client=llm,
    )

    response = agent.execute(
        AgentRequest(question=question),
        trace_id=_TRACE_ID,
    )

    calls_by_name = {
        "search_notes": notes.calls,
        "get_project_context": project.calls,
        "get_resume_context": resume.calls,
    }
    assert response.status is AgentStatus.SUCCESS
    assert response.intent is intent
    assert response.citations[0].source_namespace == namespace
    assert response.confidence is AgentConfidence.MEDIUM
    assert response.follow_up_questions
    assert len(calls_by_name[selected_tool]) == 1
    assert sum(bool(calls) for calls in calls_by_name.values()) == 1

    payload = json.loads(llm.calls[0][1].content)
    assert payload["intent"] == intent.value
    assert payload["prompt_version"] == "grounded-answer-v2"
    assert payload["evidence_context"]["evidence"][0]["content"] == content
    assert (
        payload["evidence_context"]["evidence"][0]["source"]["namespace"]
        == namespace
    )


def test_grounded_question_is_redacted_only_before_remote_llm() -> None:
    """原问题用于本地路由和检索，常见联系方式不进入远端提示词。"""
    project = FakeScopedTool(
        _tool_response(
            "get_project_context",
            "projects",
            content="项目当前实现单 Reactor。",
        )
    )
    llm = FakeLLM("项目当前实现单 Reactor。[S1]")
    question = "我的项目 candidate@example.com 当前实现状态是什么？"
    response = KnowledgeAgent(
        search_notes=FakeScopedTool(
            _tool_response("search_notes", "notes", content="笔记")
        ),
        get_project_context=project,
        llm_client=llm,
    ).execute(
        AgentRequest(question=question),
        trace_id=_TRACE_ID,
    )
    assert response.status is AgentStatus.SUCCESS
    assert project.calls[0][0].query == question
    payload = json.loads(llm.calls[0][1].content)
    assert "candidate@example.com" not in payload["question"]
    assert "[REDACTED_EMAIL]" in payload["question"]


def test_agent_rejects_tool_identity_or_namespace_confusion() -> None:
    """路由后的 Tool 名称和证据 namespace 必须同时匹配固定策略。"""
    notes = FakeScopedTool(
        _tool_response("search_notes", "notes", content="通用笔记")
    )
    forged_name = FakeScopedTool(
        _tool_response("search_notes", "projects", content="项目资料")
    )
    llm = FakeLLM("不应被调用 [S1]")
    agent = KnowledgeAgent(
        search_notes=notes,
        get_project_context=forged_name,
        llm_client=llm,
    )
    response = agent.execute(
        AgentRequest(question="我的项目当前实现状态是什么？"),
        trace_id=_TRACE_ID,
    )
    assert response.status is AgentStatus.INTERNAL_ERROR
    assert response.error.code == "context_build_failed"
    assert llm.calls == []

    forged_namespace = FakeScopedTool(
        _tool_response(
            "get_project_context",
            "resume",
            content="不应越权的简历资料",
        )
    )
    response = KnowledgeAgent(
        search_notes=notes,
        get_project_context=forged_namespace,
        llm_client=llm,
    ).execute(
        AgentRequest(question="我的项目当前实现状态是什么？"),
        trace_id=_TRACE_ID,
    )
    assert response.status is AgentStatus.INTERNAL_ERROR
    assert response.error.code == "context_build_failed"
    assert llm.calls == []


def test_confidence_counts_distinct_source_files_not_chunks() -> None:
    """同一文件的多个片段不能伪装成多个独立来源。"""
    base = _tool_response(
        "get_project_context",
        "projects",
        content="项目片段一",
    )
    first = base.results[0]
    second_same_file = replace(
        first,
        rank=2,
        chunk_id="projects-chunk-2",
        content="项目片段二",
        start_line=4,
        end_line=6,
    )
    same_source_tool = FakeScopedTool(
        replace(base, results=(first, second_same_file))
    )
    llm = FakeLLM("两个片段共同支持结论。[S1][S2]")
    response = KnowledgeAgent(
        search_notes=FakeScopedTool(
            _tool_response("search_notes", "notes", content="笔记")
        ),
        get_project_context=same_source_tool,
        llm_client=llm,
    ).execute(
        AgentRequest(question="我的项目当前实现状态是什么？"),
        trace_id=_TRACE_ID,
    )
    assert response.status is AgentStatus.SUCCESS
    assert response.confidence is AgentConfidence.MEDIUM

    second_file = replace(
        second_same_file,
        document_id="projects-document-2",
        relative_path="architecture.md",
    )
    different_source_tool = FakeScopedTool(
        replace(base, results=(first, second_file))
    )
    response = KnowledgeAgent(
        search_notes=FakeScopedTool(
            _tool_response("search_notes", "notes", content="笔记")
        ),
        get_project_context=different_source_tool,
        llm_client=llm,
    ).execute(
        AgentRequest(question="我的项目当前实现状态是什么？"),
        trace_id=_TRACE_ID,
    )
    assert response.status is AgentStatus.SUCCESS
    assert response.confidence is AgentConfidence.HIGH


def test_interview_review_redacts_contacts_and_does_not_call_tools() -> None:
    """面试记录在进入 LLM 前脱敏，复盘不伪造知识库引用。"""
    notes = FakeScopedTool(
        _tool_response("search_notes", "notes", content="通用笔记")
    )
    llm = FakeLLM(
        "## 问题归纳\n智能指针。\n## 回答表现\n遗漏所有权。\n"
        "## 暴露短板\nRAII 解释不完整。\n"
        "## 后续行动\n补充最小代码示例。"
    )
    agent = KnowledgeAgent(search_notes=notes, llm_client=llm)
    record = (
        "面试官问智能指针，我回答了 shared_ptr。\n"
        "邮箱：candidate@example.com\n手机：13812345678"
    )

    response = agent.execute(
        AgentRequest(
            question="请复盘 candidate@example.com 的这场面试",
            interview_record=record,
        ),
        trace_id=_TRACE_ID,
    )

    assert response.status is AgentStatus.SUCCESS
    assert response.intent is AgentIntent.INTERVIEW_REVIEW
    assert response.citations == ()
    assert response.tool_call_ids == ()
    assert response.confidence is AgentConfidence.NOT_APPLICABLE
    assert notes.calls == []
    payload = json.loads(llm.calls[0][1].content)
    assert "candidate@example.com" not in payload["question"]
    assert "[REDACTED_EMAIL]" in payload["question"]
    assert "candidate@example.com" not in payload["interview_record"]
    assert "13812345678" not in payload["interview_record"]
    assert "[REDACTED_EMAIL]" in payload["interview_record"]
    assert "[REDACTED_PHONE]" in payload["interview_record"]


def test_interview_review_requires_record_and_rejects_unsafe_output() -> None:
    """没有记录不猜测；带伪引用或外链的复盘结果不能返回。"""
    notes = FakeScopedTool(
        _tool_response("search_notes", "notes", content="通用笔记")
    )
    llm = FakeLLM("不应被调用")
    agent = KnowledgeAgent(search_notes=notes, llm_client=llm)
    missing = agent.execute(
        AgentRequest(question="请复盘这场面试"),
        trace_id=_TRACE_ID,
    )
    assert missing.status is AgentStatus.INVALID_INPUT
    assert missing.error.code == "interview_record_required"
    assert llm.calls == []

    unsafe_llm = FakeLLM(
        "## 问题归纳\n问题\n## 回答表现\n表现\n"
        "## 暴露短板\n短板\n## 后续行动\n行动 [S1]"
    )
    unsafe = KnowledgeAgent(
        search_notes=notes,
        llm_client=unsafe_llm,
    ).execute(
        AgentRequest(
            question="复盘",
            interview_record="面试官问了 RAII，我没有回答完整。",
        ),
        trace_id=_TRACE_ID,
    )
    assert unsafe.status is AgentStatus.INVALID_OUTPUT
    assert unsafe.error.code == "unexpected_citation"

    missing_section_llm = FakeLLM("只有一段笼统结论")
    missing_section = KnowledgeAgent(
        search_notes=notes,
        llm_client=missing_section_llm,
    ).execute(
        AgentRequest(
            question="复盘",
            interview_record="面试官问了 RAII，我没有回答完整。",
        ),
        trace_id=_TRACE_ID,
    )
    assert missing_section.status is AgentStatus.INVALID_OUTPUT
    assert missing_section.error.code == "invalid_review_structure"


def test_interview_record_length_is_bounded_before_llm() -> None:
    """超长面试记录不会进入提示词或远端请求。"""
    notes = FakeScopedTool(
        _tool_response("search_notes", "notes", content="通用笔记")
    )
    llm = FakeLLM("不应被调用")
    response = KnowledgeAgent(
        search_notes=notes,
        llm_client=llm,
    ).execute(
        AgentRequest(
            question="复盘",
            interview_record="问" * (MAX_INTERVIEW_RECORD_CHARACTERS + 1),
        ),
        trace_id=_TRACE_ID,
    )
    assert response.status is AgentStatus.INVALID_INPUT
    assert response.error.code == "interview_record_too_long"
    assert llm.calls == []
