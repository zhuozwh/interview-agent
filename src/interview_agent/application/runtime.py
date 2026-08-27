"""组装本地索引、三个只读 Tool、LLM 和问答用例。"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Protocol

from interview_agent.agent import AgentRequest, KnowledgeAgent
from interview_agent.application.ask import AskInterviewAgentUseCase
from interview_agent.application.models import AskResult
from interview_agent.core.config import Settings
from interview_agent.llm import LLMClient, OpenAICompatibleLLMClient
from interview_agent.retrieval import (
    EmbeddingProvider,
    FastEmbedEmbeddingProvider,
    IndexDocument,
    VectorSyncReport,
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


class AskService(Protocol):
    """FastAPI 只依赖的一次问答应用边界。"""

    def execute(
        self,
        request: AgentRequest,
        *,
        session_id: str | None = None,
    ) -> AskResult:
        """执行一次问答或复盘。"""


class ApplicationUnavailableError(RuntimeError):
    """本地数据、索引或 LLM 配置尚不能建立应用运行时。"""


class LocalInterviewRuntime:
    """持有需要显式关闭的 Chroma 和 HTTP 连接池。"""

    def __init__(
        self,
        *,
        use_case: AskInterviewAgentUseCase,
        vector_store: ChromaVectorStore,
        llm_client: LLMClient,
        sync_report: VectorSyncReport,
    ) -> None:
        self.use_case = use_case
        self.vector_store = vector_store
        self.llm_client = llm_client
        self.sync_report = sync_report
        self._closed = False
        # Phase 1 是本地单用户应用；串行化共享模型、Chroma 和 SQLite 的访问，
        # 避免把第三方对象的线程安全当成未经验证的隐含前提。
        self._execute_lock = Lock()

    def execute(
        self,
        request: AgentRequest,
        *,
        session_id: str | None = None,
    ) -> AskResult:
        """把请求交给已经组装好的应用用例。"""
        with self._execute_lock:
            if self._closed:
                raise ApplicationUnavailableError("The local runtime is closed.")
            return self.use_case.execute(request, session_id=session_id)

    def close(self) -> None:
        """幂等释放本地向量库和 LLM HTTP 客户端。"""
        with self._execute_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self.vector_store.close()
            finally:
                close_llm = getattr(self.llm_client, "close", None)
                if callable(close_llm):
                    close_llm()


class LazyLocalAskService:
    """第一次请求时才加载模型和索引，健康检查不会触发下载。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = Lock()
        self._runtime: LocalInterviewRuntime | None = None
        self._unavailable = False

    def execute(
        self,
        request: AgentRequest,
        *,
        session_id: str | None = None,
    ) -> AskResult:
        """线程安全地建立一次运行时，失败后要求修正配置并重启。"""
        runtime = self._get_runtime()
        return runtime.execute(request, session_id=session_id)

    def close(self) -> None:
        """应用关闭时释放已经建立的运行时。"""
        with self._lock:
            if self._runtime is not None:
                try:
                    self._runtime.close()
                finally:
                    self._runtime = None

    def _get_runtime(self) -> LocalInterviewRuntime:
        if self._runtime is not None:
            return self._runtime
        if self._unavailable:
            raise ApplicationUnavailableError(
                "The local application runtime is unavailable."
            )
        with self._lock:
            if self._runtime is not None:
                return self._runtime
            try:
                self._runtime = build_local_runtime(self.settings)
            except Exception as error:
                # 不把路径、密钥或第三方异常正文穿透到 HTTP 响应。
                self._unavailable = True
                raise ApplicationUnavailableError(
                    "The local application runtime could not be initialized."
                ) from error
            return self._runtime


def build_local_runtime(settings: Settings) -> LocalInterviewRuntime:
    """按依赖方向组装真实本地运行时，并先完成三数据源增量同步。"""
    if not isinstance(settings, Settings):
        raise ValueError("settings must be a Settings instance")
    if settings.llm_api_key is None:
        raise ApplicationUnavailableError("LLM_API_KEY is not configured.")

    source_paths = validate_local_storage_boundaries(settings)
    database = SQLiteDatabase(settings.database_path)
    state_store = SQLiteIndexStateStore(database)
    tool_trace_store = SQLiteToolTraceStore(database)
    agent_trace_store = SQLiteAgentTraceStore(database)
    state_store.initialize()
    tool_trace_store.initialize()
    agent_trace_store.initialize()

    embedding_provider = FastEmbedEmbeddingProvider(
        model_name=settings.embedding_model_name,
        cache_directory=settings.embedding_cache_directory,
        local_files_only=settings.embedding_local_files_only,
    )
    vector_store = ChromaVectorStore(
        settings.vector_store_path,
        collection_name=settings.vector_collection_name,
    )
    llm_client: OpenAICompatibleLLMClient | None = None
    try:
        documents = _load_index_documents(
            source_paths,
            allowed_directories=settings.allowed_data_directories,
            max_file_size_bytes=settings.markdown_max_file_size_bytes,
            max_total_size_bytes=settings.markdown_max_total_size_bytes,
            max_chunk_characters=settings.markdown_chunk_max_characters,
        )
        plan = build_index_plan(
            documents,
            state_store.load_document_states(),
        )
        sync_report = synchronize_vector_index(
            plan,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            state_store=state_store,
            batch_size=settings.embedding_batch_size,
        )
        llm_client = OpenAICompatibleLLMClient(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            thinking_mode=settings.llm_thinking_mode,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
        search_notes = SearchNotesTool(
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=tool_trace_store,
            min_score=settings.search_notes_min_score,
            max_total_characters=settings.search_notes_max_total_characters,
        )
        project_tool = GetProjectContextTool(
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=tool_trace_store,
            min_score=settings.project_context_min_score,
            max_total_characters=(
                settings.project_context_max_total_characters
            ),
        )
        resume_tool = GetResumeContextTool(
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=tool_trace_store,
            min_score=settings.resume_context_min_score,
            max_total_characters=settings.resume_context_max_total_characters,
        )
        agent = KnowledgeAgent(
            search_notes=search_notes,
            get_project_context=project_tool,
            get_resume_context=resume_tool,
            llm_client=llm_client,
            context_max_characters=settings.rag_context_max_characters,
            top_k=settings.agent_top_k,
            max_answer_characters=settings.agent_max_answer_characters,
        )
        use_case = AskInterviewAgentUseCase(
            agent=agent,
            trace_store=agent_trace_store,
        )
        return LocalInterviewRuntime(
            use_case=use_case,
            vector_store=vector_store,
            llm_client=llm_client,
            sync_report=sync_report,
        )
    except Exception:
        vector_store.close()
        if llm_client is not None:
            llm_client.close()
        raise


def _require_disjoint_source_directories(
    source_directories: tuple[Path, Path, Path],
) -> tuple[Path, Path, Path]:
    """解析真实路径并拒绝相同或相互包含的数据源。"""
    resolved: list[Path] = []
    for source in source_directories:
        try:
            path = source.expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ApplicationUnavailableError(
                "A configured source directory cannot be resolved."
            ) from error
        if not path.is_dir():
            raise ApplicationUnavailableError(
                "A configured source path is not a directory."
            )
        resolved.append(path)

    for index, left in enumerate(resolved):
        for right in resolved[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise ApplicationUnavailableError(
                    "Configured source directories must not overlap."
                )
    return resolved[0], resolved[1], resolved[2]


def validate_local_storage_boundaries(
    settings: Settings,
) -> tuple[Path, Path, Path]:
    """在任何本地状态写入前验证只读数据源与运行时路径隔离。"""
    if not isinstance(settings, Settings):
        raise ValueError("settings must be a Settings instance")
    source_paths = _require_disjoint_source_directories(
        (
            settings.markdown_source_directory,
            settings.project_source_directory,
            settings.resume_source_directory,
        )
    )
    _require_runtime_paths_outside_sources(settings, source_paths)
    return source_paths


def _require_runtime_paths_outside_sources(
    settings: Settings,
    source_directories: tuple[Path, Path, Path],
) -> None:
    """在创建任何运行时文件前拒绝与只读数据源重叠的写入路径。"""
    runtime_paths = (
        ("DATABASE_PATH", settings.database_path, False),
        ("VECTOR_STORE_PATH", settings.vector_store_path, True),
        ("EMBEDDING_CACHE_DIRECTORY", settings.embedding_cache_directory, True),
    )
    for label, configured_path, is_directory in runtime_paths:
        try:
            runtime_path = configured_path.expanduser().resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise ApplicationUnavailableError(
                f"{label} cannot be resolved safely."
            ) from error

        for source_path in source_directories:
            # 运行时文件不能写进数据源；目录型运行时根也不能反向包含数据源，
            # 否则后续重建或人工清理运行时目录时可能误伤原始 Markdown。
            overlaps = (
                runtime_path == source_path
                or source_path in runtime_path.parents
                or (is_directory and runtime_path in source_path.parents)
            )
            if overlaps:
                raise ApplicationUnavailableError(
                    "Runtime storage paths must stay outside configured "
                    "source directories."
                )


def _load_index_documents(
    source_directories: tuple[Path, Path, Path],
    *,
    allowed_directories: tuple[Path, ...],
    max_file_size_bytes: int,
    max_total_size_bytes: int,
    max_chunk_characters: int,
) -> tuple[IndexDocument, ...]:
    """稳定加载三个 namespace，并对合计原始 UTF-8 字节数再次限流。"""
    prepared: list[IndexDocument] = []
    total_size_bytes = 0
    namespaces = ("notes", "projects", "resume")
    for source, namespace in zip(
        source_directories,
        namespaces,
        strict=True,
    ):
        documents = load_markdown_documents(
            source,
            allowed_directories,
            max_file_size_bytes=max_file_size_bytes,
            max_total_size_bytes=max_total_size_bytes,
        )
        total_size_bytes += sum(
            len(document.content.encode("utf-8")) for document in documents
        )
        if total_size_bytes > max_total_size_bytes:
            raise ApplicationUnavailableError(
                "Combined Markdown sources exceed the configured total limit."
            )
        prepared.extend(
            prepare_index_documents(
                documents,
                max_chunk_characters=max_chunk_characters,
                source_namespace=namespace,
            )
        )
    return tuple(prepared)


__all__ = [
    "ApplicationUnavailableError",
    "AskService",
    "LazyLocalAskService",
    "LocalInterviewRuntime",
    "build_local_runtime",
    "validate_local_storage_boundaries",
]
