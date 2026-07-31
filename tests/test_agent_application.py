"""验证应用用例的会话生命周期和无正文 SQLite 追踪。"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.agent import (
    AgentConfidence,
    AgentIntent,
    AgentRequest,
    AgentResponse,
    AgentStatus,
)
from interview_agent.application import AskInterviewAgentUseCase
from interview_agent.storage import SQLiteAgentTraceStore, SQLiteDatabase

_SESSION_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """SQLite 测试数据使用退出后自动清理的目录。"""
    with TemporaryDirectory(prefix="interview-agent-app-test-") as directory:
        yield Path(directory)


class FakeAgent:
    """返回与应用传入 trace_id 一致的固定复盘结果。"""

    def __init__(self) -> None:
        self.calls = []

    def execute(self, request, *, trace_id=None):
        self.calls.append((request, trace_id))
        return AgentResponse(
            trace_id=trace_id,
            status=AgentStatus.SUCCESS,
            intent=AgentIntent.INTERVIEW_REVIEW,
            route_reason="provided_interview_record_requires_review",
            answer="已生成安全复盘。",
            citations=(),
            tool_call_ids=(),
            llm_request_id="provider-request-1",
            error=None,
            confidence=AgentConfidence.NOT_APPLICABLE,
            follow_up_questions=("下一次如何改进？",),
        )


def _create_use_case(temporary_directory: Path):
    """创建真实 SQLite 追踪存储和确定性 Agent。"""
    database = SQLiteDatabase(temporary_directory / "agent.sqlite3")
    trace_store = SQLiteAgentTraceStore(database)
    trace_store.initialize()
    agent = FakeAgent()
    return (
        AskInterviewAgentUseCase(agent=agent, trace_store=trace_store),
        agent,
        trace_store,
        database,
    )


def test_use_case_reuses_session_and_records_only_safe_metadata(
    temporary_directory: Path,
) -> None:
    """同一 session 可关联多次请求，SQLite 不保存请求和回答正文。"""
    use_case, agent, trace_store, database = _create_use_case(
        temporary_directory
    )
    question = "请复盘 candidate@example.com 的这场面试"
    record = "面试官问 RAII，手机号 13812345678，我回答不完整。"

    first = use_case.execute(
        AgentRequest(question=question, interview_record=record),
        session_id=_SESSION_ID,
    )
    second = use_case.execute(
        AgentRequest(question="继续复盘", interview_record="补充记录"),
        session_id=_SESSION_ID,
    )

    assert first.session_id == second.session_id == _SESSION_ID
    assert first.response.status is AgentStatus.SUCCESS
    assert len(agent.calls) == 2
    assert agent.calls[0][1] == first.response.trace_id
    records = trace_store.load_records(session_id=_SESSION_ID)
    assert len(records) == 2
    assert records[0].question_length == len(question)
    assert records[0].interview_record_length == len(record)
    serialized = repr(records)
    assert "candidate@example.com" not in serialized
    assert "13812345678" not in serialized
    assert "已生成安全复盘" not in serialized

    with database.connection() as connection:
        session_count = connection.execute(
            "SELECT COUNT(*) FROM agent_sessions"
        ).fetchone()[0]
    assert session_count == 1


def test_invalid_session_stops_before_agent_but_is_still_traced(
    temporary_directory: Path,
) -> None:
    """非法外部会话 ID 不进入主键，生成内部 ID 后记录失败原因。"""
    use_case, agent, trace_store, _ = _create_use_case(temporary_directory)
    result = use_case.execute(
        AgentRequest(question="智能指针是什么？"),
        session_id="../bad-session",
    )

    assert result.session_id != "../bad-session"
    assert result.response.status is AgentStatus.INVALID_INPUT
    assert result.response.error.code == "invalid_session_id"
    assert agent.calls == []
    record = trace_store.load_records(trace_id=result.response.trace_id)[0]
    assert record.error_code == "invalid_session_id"
    assert record.question_length == len("智能指针是什么？")


def test_trace_write_failure_removes_untracked_success() -> None:
    """审计写入失败时不能把已经生成的正文继续作为成功结果返回。"""

    class FailingTraceStore:
        def record(self, trace):
            raise sqlite3.DatabaseError("test failure")

    agent = FakeAgent()
    result = AskInterviewAgentUseCase(
        agent=agent,
        trace_store=FailingTraceStore(),
    ).execute(AgentRequest(question="复盘", interview_record="面试记录"))

    assert result.response.status is AgentStatus.INTERNAL_ERROR
    assert result.response.answer is None
    assert result.response.error.code == "trace_write_failed"
    assert result.response.llm_request_id is None


def test_agent_trace_primary_key_is_append_only(
    temporary_directory: Path,
) -> None:
    """重复 trace_id 明确失败，不能覆盖已有审计历史。"""
    use_case, _, trace_store, _ = _create_use_case(temporary_directory)
    result = use_case.execute(AgentRequest(question="智能指针是什么？"))
    trace = trace_store.load_records(trace_id=result.response.trace_id)[0]

    with pytest.raises(sqlite3.IntegrityError):
        trace_store.record(trace)
