"""以真实本地存储贯通 Phase 1 的加载、RAG、Agent、追踪和 HTTP。"""

import asyncio
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest

from interview_agent.agent import KnowledgeAgent
from interview_agent.application import AskInterviewAgentUseCase
from interview_agent.core.config import Settings
from interview_agent.llm import LLMResponse, LLMUsage
from interview_agent.main import create_app
from interview_agent.retrieval import (
    build_index_plan,
    load_markdown_documents,
    prepare_index_documents,
    synchronize_vector_index,
)
from interview_agent.storage import (
    ChromaVectorStore,
    SQLiteAgentTraceStore,
    SQLiteDatabase,
    SQLiteIndexStateStore,
    SQLiteToolTraceStore,
)
from interview_agent.tools import (
    GetProjectContextTool,
    GetResumeContextTool,
    SearchNotesTool,
)

_SESSION_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """整条验收流水线只使用自动清理的本地临时目录。"""
    with TemporaryDirectory(prefix="interview-agent-phase1-test-") as directory:
        yield Path(directory)


class WorkflowEmbedding:
    """按三个资料主题生成确定向量，避免下载真实模型。"""

    model_name = "phase1-workflow-embedding-v1"
    dimension = 4

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self._vector(query)

    @staticmethod
    def _vector(text: str) -> list[float]:
        if "智能指针" in text or "RAII" in text:
            return [1.0, 0.01, 0.01, 0.01]
        if "事件循环" in text or "Reactor" in text:
            return [0.01, 1.0, 0.01, 0.01]
        if "实习" in text or "后端经历" in text:
            return [0.01, 0.01, 1.0, 0.01]
        return [0.01, 0.01, 0.01, 1.0]


class WorkflowLLM:
    """根据提示类型返回合法固定答案，并保留实际远端载荷形状。"""

    def __init__(self) -> None:
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        payload = json.loads(messages[1].content)
        if payload["prompt_version"] == "interview-review-v1":
            content = (
                "## 问题归纳\n考察了 RAII。\n"
                "## 回答表现\n记录显示回答不完整。\n"
                "## 暴露短板\n所有权说明不足。\n"
                "## 后续行动\n补充一个最小代码示例。"
            )
        else:
            content = "根据本地资料可以得到这个结论。[S1]"
        return LLMResponse(
            request_id=f"workflow-request-{len(self.calls)}",
            model="test-model",
            content=content,
            finish_reason="stop",
            system_fingerprint=None,
            usage=LLMUsage(
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            ),
        )


async def _post(application, payload):
    """在内存中调用 FastAPI，不启动网络监听。"""
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.post("/ask", json=payload)


def test_phase1_local_workflow_handles_all_four_intents(
    temporary_directory: Path,
) -> None:
    """三个只读 Tool 和无 Tool 复盘共享会话、追踪及安全响应。"""
    allowed = temporary_directory / "knowledge"
    notes = allowed / "interview"
    projects = allowed / "projects"
    resume = allowed / "resume"
    notes.mkdir(parents=True)
    projects.mkdir()
    resume.mkdir()
    (notes / "memory.md").write_text(
        "# 智能指针\nRAII 使用对象生命周期管理资源。",
        encoding="utf-8",
    )
    (projects / "server.md").write_text(
        "# 事件循环\n当前项目实现单 Reactor。",
        encoding="utf-8",
    )
    (resume / "resume.md").write_text(
        "# 后端经历\n实习期间实现服务模块。\n"
        "邮箱：candidate@example.com\n手机：13812345678",
        encoding="utf-8",
    )

    prepared = []
    for source, namespace in (
        (notes, "notes"),
        (projects, "projects"),
        (resume, "resume"),
    ):
        prepared.extend(
            prepare_index_documents(
                load_markdown_documents(source, (allowed,)),
                max_chunk_characters=500,
                source_namespace=namespace,
            )
        )

    database = SQLiteDatabase(temporary_directory / "state.sqlite3")
    state_store = SQLiteIndexStateStore(database)
    tool_trace_store = SQLiteToolTraceStore(database)
    agent_trace_store = SQLiteAgentTraceStore(database)
    state_store.initialize()
    tool_trace_store.initialize()
    agent_trace_store.initialize()
    embedding = WorkflowEmbedding()
    llm = WorkflowLLM()

    with ChromaVectorStore(temporary_directory / "chroma") as vector_store:
        synchronize_vector_index(
            build_index_plan(prepared, ()),
            embedding_provider=embedding,
            vector_store=vector_store,
            state_store=state_store,
        )
        agent = KnowledgeAgent(
            search_notes=SearchNotesTool(
                embedding_provider=embedding,
                vector_store=vector_store,
                state_store=state_store,
                trace_store=tool_trace_store,
                min_score=0.5,
            ),
            get_project_context=GetProjectContextTool(
                embedding_provider=embedding,
                vector_store=vector_store,
                state_store=state_store,
                trace_store=tool_trace_store,
                min_score=0.5,
            ),
            get_resume_context=GetResumeContextTool(
                embedding_provider=embedding,
                vector_store=vector_store,
                state_store=state_store,
                trace_store=tool_trace_store,
                min_score=0.5,
            ),
            llm_client=llm,
        )
        application = create_app(
            Settings(_env_file=None),
            ask_service=AskInterviewAgentUseCase(
                agent=agent,
                trace_store=agent_trace_store,
            ),
        )
        payloads = (
            {"question": "智能指针如何体现 RAII？"},
            {"question": "我的项目事件循环当前实现状态是什么？"},
            {"question": "我的简历里有哪些后端实习经历？"},
            {
                "question": "请复盘这场面试",
                "interview_record": (
                    "面试官问 RAII，我没有说清所有权。"
                    "邮箱 candidate@example.com"
                ),
            },
        )
        responses = [
            asyncio.run(
                _post(
                    application,
                    {**payload, "session_id": _SESSION_ID},
                )
            )
            for payload in payloads
        ]

    assert [response.status_code for response in responses] == [200] * 4
    bodies = [response.json() for response in responses]
    assert [body["intent"] for body in bodies] == [
        "knowledge_question",
        "project_context",
        "resume_context",
        "interview_review",
    ]
    assert [
        body["citations"][0]["source_namespace"] for body in bodies[:3]
    ] == ["notes", "projects", "resume"]
    assert bodies[3]["citations"] == []
    assert bodies[3]["tool_call_ids"] == []
    assert all(body["session_id"] == _SESSION_ID for body in bodies)

    resume_prompt = llm.calls[2][1].content
    review_prompt = llm.calls[3][1].content
    assert "candidate@example.com" not in resume_prompt
    assert "13812345678" not in resume_prompt
    assert "candidate@example.com" not in review_prompt

    tool_traces = tool_trace_store.load_records()
    assert [trace.tool_name for trace in tool_traces] == [
        "search_notes",
        "get_project_context",
        "get_resume_context",
    ]
    agent_traces = agent_trace_store.load_records(session_id=_SESSION_ID)
    assert len(agent_traces) == 4
    assert [trace.status for trace in agent_traces] == ["success"] * 4
    assert all("candidate@example.com" not in repr(trace) for trace in agent_traces)
