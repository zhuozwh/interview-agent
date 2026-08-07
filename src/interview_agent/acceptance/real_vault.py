"""在不调用远端 LLM 的前提下验收真实 Vault 检索边界。"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter
from typing import Any

from interview_agent.agent import (
    AgentIntent,
    AgentRequest,
    AgentStatus,
    KnowledgeAgent,
    route_question,
)
from interview_agent.core.config import Settings
from interview_agent.core.privacy import redact_common_personal_data
from interview_agent.llm import LLMResponse, LLMUsage
from interview_agent.rag import RagContextStatus, build_scoped_search_context
from interview_agent.retrieval import (
    EmbeddingProvider,
    FastEmbedEmbeddingProvider,
    IndexDocument,
    build_index_plan,
    load_markdown_documents,
    prepare_index_documents,
    search_chunks,
    synchronize_vector_index,
)
from interview_agent.storage import (
    ChromaVectorStore,
    SQLiteDatabase,
    SQLiteIndexStateStore,
    SQLiteToolTraceStore,
)
from interview_agent.tools import (
    GetProjectContextTool,
    GetResumeContextTool,
    SearchNotesTool,
)
from interview_agent.tools.scoped_search import (
    ScopedSearchRequest,
    ScopedSearchResponse,
    ScopedSearchStatus,
)

_SCHEMA_VERSION = 1
_REQUIRED_NAMESPACES = ("notes", "projects", "resume")
_CASE_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]{1,31}$")
_MAX_CASE_FILE_BYTES = 1024 * 1024
_MAX_CASES = 500
_MAX_PROBES_PER_CASE = 3
_MAX_QUESTION_CHARACTERS = 480
_SAFE_RELATIVE_PATH_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]+$")


class VaultAcceptanceError(RuntimeError):
    """真实 Vault 验收无法安全执行。"""


class ProbeCategory(StrEnum):
    """区分正例、技术型硬负例和跨 namespace 对抗用例。"""

    POSITIVE = "positive"
    HARD_NEGATIVE = "hard_negative"
    CROSS_NAMESPACE = "cross_namespace"


class ProbeExpectation(StrEnum):
    """一个 namespace 探针期望成功召回或明确无结果。"""

    SUCCESS = "success"
    NO_RESULTS = "no_results"


@dataclass(frozen=True, slots=True)
class AcceptanceProbe:
    """针对固定 namespace 的匿名检索期望。"""

    namespace: str
    category: ProbeCategory
    expectation: ProbeExpectation
    expected_paths: tuple[str, ...] = ()
    critical: bool = False


@dataclass(frozen=True, slots=True)
class AcceptanceCase:
    """本地问题、路由期望和一个或多个检索探针。"""

    case_id: str
    question: str
    expected_intent: AgentIntent
    probes: tuple[AcceptanceProbe, ...]


@dataclass(frozen=True, slots=True)
class _SourceSnapshotEntry:
    """只用于前后比较，不进入匿名报告的文件系统记录。"""

    namespace: str
    relative_path: str
    entry_type: str
    size_bytes: int
    modified_time_ns: int
    content_sha256: str | None


class _AcceptanceLLM:
    """只返回合法固定引用，并保留调用载荷供隐私边界检查。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def complete(self, messages) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(
            request_id=f"acceptance-request-{len(self.calls)}",
            model="deterministic-acceptance-stub",
            content="验收占位回答，仅用于验证引用映射。[S1]",
            finish_reason="stop",
            system_fingerprint=None,
            usage=LLMUsage(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            ),
        )


def load_acceptance_cases(path: str | Path) -> tuple[AcceptanceCase, ...]:
    """严格读取本地 JSON 问题集，不在异常中回显私人正文。"""
    case_path = Path(path)
    try:
        size_bytes = case_path.stat().st_size
    except OSError as error:
        raise VaultAcceptanceError(
            "The local acceptance case file is unavailable."
        ) from error
    if size_bytes <= 0 or size_bytes > _MAX_CASE_FILE_BYTES:
        raise VaultAcceptanceError(
            "The local acceptance case file has an invalid size."
        )
    try:
        payload = json.loads(case_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VaultAcceptanceError(
            "The local acceptance case file is not valid UTF-8 JSON."
        ) from error
    try:
        return _parse_case_payload(payload)
    except (KeyError, TypeError, ValueError) as error:
        raise VaultAcceptanceError(
            "The local acceptance case file does not match schema version 1."
        ) from error


def run_real_vault_acceptance(
    settings: Settings,
    cases: Sequence[AcceptanceCase],
    *,
    report_path: str | Path,
    allow_incomplete_sources: bool = False,
    embedding_provider: EmbeddingProvider | None = None,
) -> dict[str, Any]:
    """运行真实加载、索引、Tool 和 Agent 边界，并写出匿名 JSON 报告。"""
    if not isinstance(settings, Settings):
        raise VaultAcceptanceError("settings must be a Settings instance")
    if not cases:
        raise VaultAcceptanceError("At least one acceptance case is required.")
    if not isinstance(allow_incomplete_sources, bool):
        raise VaultAcceptanceError("allow_incomplete_sources must be boolean")
    if settings.llm_api_key is not None:
        raise VaultAcceptanceError(
            "Offline Vault acceptance requires LLM_API_KEY to be unset."
        )
    if not settings.embedding_local_files_only:
        raise VaultAcceptanceError(
            "Offline Vault acceptance requires EMBEDDING_LOCAL_FILES_ONLY=true."
        )

    report_file = Path(report_path).expanduser().resolve(strict=False)
    source_paths, missing_namespaces = _resolve_source_paths(
        settings,
        allow_incomplete_sources=allow_incomplete_sources,
    )
    _validate_runtime_boundaries(settings, source_paths, report_file)
    _validate_case_coverage(
        cases,
        loaded_namespaces=tuple(source_paths),
        formal_complete=not missing_namespaces,
    )

    before_snapshot = _snapshot_sources(source_paths)
    started = perf_counter()
    provider = embedding_provider or FastEmbedEmbeddingProvider(
        model_name=settings.embedding_model_name,
        cache_directory=settings.embedding_cache_directory,
        local_files_only=True,
    )
    report = _execute_acceptance(
        settings,
        cases,
        source_paths=source_paths,
        missing_namespaces=missing_namespaces,
        before_snapshot=before_snapshot,
        embedding_provider=provider,
    )
    after_snapshot = _snapshot_sources(source_paths)
    vault_unchanged = before_snapshot == after_snapshot
    report["vault_unchanged"] = vault_unchanged
    report["source_manifest_sha256"] = _snapshot_fingerprint(after_snapshot)
    report["duration_ms"] = round((perf_counter() - started) * 1000)
    report["safety_passed"] = bool(
        report["safety_passed"] and vault_unchanged
    )
    report["acceptance_passed"] = bool(
        report["formal_complete"]
        and report["safety_passed"]
        and report["quality_gates_passed"]
    )
    if not vault_unchanged:
        report["failures"].append(
            {"case_id": "VAULT", "code": "source_manifest_changed"}
        )

    _write_sanitized_report(
        report_file,
        report,
        cases=cases,
        source_paths=tuple(source_paths.values()),
    )
    return report


def _execute_acceptance(
    settings: Settings,
    cases: Sequence[AcceptanceCase],
    *,
    source_paths: Mapping[str, Path],
    missing_namespaces: tuple[str, ...],
    before_snapshot: tuple[_SourceSnapshotEntry, ...],
    embedding_provider: EmbeddingProvider,
) -> dict[str, Any]:
    """把真实资料建立到隔离索引，并执行匿名问题集。"""
    documents, source_stats = _load_index_documents(
        settings,
        source_paths=source_paths,
    )
    database = SQLiteDatabase(settings.database_path)
    state_store = SQLiteIndexStateStore(database)
    trace_store = SQLiteToolTraceStore(database)
    state_store.initialize()
    trace_store.initialize()

    failures: list[dict[str, str]] = []
    case_results: list[dict[str, Any]] = []
    first_plan = build_index_plan(
        documents,
        state_store.load_document_states(),
    )
    llm = _AcceptanceLLM()

    with ChromaVectorStore(
        settings.vector_store_path,
        collection_name=settings.vector_collection_name,
    ) as vector_store:
        first_sync = synchronize_vector_index(
            first_plan,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            state_store=state_store,
            batch_size=settings.embedding_batch_size,
        )
        second_plan = build_index_plan(
            documents,
            state_store.load_document_states(),
        )
        second_sync = synchronize_vector_index(
            second_plan,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            state_store=state_store,
            batch_size=settings.embedding_batch_size,
        )
        idempotent = (
            second_plan.change_count == 0
            and second_sync.embedded_document_count == 0
            and second_sync.embedded_chunk_count == 0
            and second_sync.deleted_document_count == 0
            and second_sync.unchanged_document_count == len(documents)
        )
        if not idempotent:
            failures.append(
                {"case_id": "INDEX", "code": "second_sync_not_idempotent"}
            )

        tools = _build_tools(
            settings,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            state_store=state_store,
            trace_store=trace_store,
        )
        agent = KnowledgeAgent(
            search_notes=tools["notes"],
            get_project_context=tools.get("projects"),
            get_resume_context=tools.get("resume"),
            llm_client=llm,
            context_max_characters=settings.rag_context_max_characters,
            top_k=settings.agent_top_k,
            max_answer_characters=settings.agent_max_answer_characters,
        )
        indexed_by_chunk = {
            chunk.chunk_id: chunk
            for document in documents
            for chunk in document.chunks
        }
        for case in cases:
            result = _evaluate_case(
                case,
                settings=settings,
                tools=tools,
                agent=agent,
                llm=llm,
                indexed_by_chunk=indexed_by_chunk,
                embedding_provider=embedding_provider,
                vector_store=vector_store,
                state_store=state_store,
            )
            case_results.append(result)
            failures.extend(result.pop("failures"))

    database_boundary_passed = _verify_database_boundary(
        settings.database_path,
        cases=cases,
        source_paths=tuple(source_paths.values()),
    )
    if not database_boundary_passed:
        failures.append(
            {"case_id": "SQLITE", "code": "sensitive_text_persisted"}
        )
    privacy_boundary_passed = _verify_llm_payloads(llm.calls)
    if not privacy_boundary_passed:
        failures.append(
            {"case_id": "PRIVACY", "code": "llm_stub_payload_not_redacted"}
        )

    metrics = _calculate_metrics(case_results)
    phase2_triggers = _phase2_triggers(metrics, case_results)
    quality_gates_passed = not phase2_triggers
    safety_codes = {
        "route_mismatch",
        "namespace_mismatch",
        "citation_invalid",
        "agent_refusal_boundary_failed",
        "resume_redaction_failed",
        "sensitive_text_persisted",
        "llm_stub_payload_not_redacted",
        "second_sync_not_idempotent",
    }
    safety_passed = not any(
        failure["code"] in safety_codes for failure in failures
    )
    return {
        "schema_version": _SCHEMA_VERSION,
        "package_version": _package_version(),
        "python_version": platform.python_version(),
        "platform": platform.system().lower(),
        "formal_complete": not missing_namespaces,
        "missing_namespaces": list(missing_namespaces),
        "case_manifest_sha256": _case_manifest_fingerprint(cases),
        "configuration_sha256": _configuration_fingerprint(settings),
        "source_counts": source_stats,
        "initial_manifest_entry_count": len(before_snapshot),
        "first_sync": {
            "profile_rebuilt": first_sync.profile_rebuilt,
            "embedded_document_count": first_sync.embedded_document_count,
            "embedded_chunk_count": first_sync.embedded_chunk_count,
            "deleted_document_count": first_sync.deleted_document_count,
            "unchanged_document_count": first_sync.unchanged_document_count,
        },
        "second_sync_idempotent": idempotent,
        "metrics": metrics,
        "phase2_triggers": phase2_triggers,
        "quality_gates_passed": quality_gates_passed,
        "database_boundary_passed": database_boundary_passed,
        "privacy_boundary_passed": privacy_boundary_passed,
        "safety_passed": safety_passed,
        "case_results": case_results,
        "failures": failures,
    }


def _evaluate_case(
    case: AcceptanceCase,
    *,
    settings: Settings,
    tools: Mapping[str, Any],
    agent: KnowledgeAgent,
    llm: _AcceptanceLLM,
    indexed_by_chunk: Mapping[str, Any],
    embedding_provider: EmbeddingProvider,
    vector_store: ChromaVectorStore,
    state_store: SQLiteIndexStateStore,
) -> dict[str, Any]:
    """分别评估路由、原始排名、Tool 契约和 Agent 停止条件。"""
    failures: list[dict[str, str]] = []
    route = route_question(case.question)
    route_passed = route.intent is case.expected_intent
    if not route_passed:
        failures.append({"case_id": case.case_id, "code": "route_mismatch"})

    probe_results: list[dict[str, Any]] = []
    for probe in case.probes:
        tool = tools.get(probe.namespace)
        if tool is None:
            failures.append(
                {"case_id": case.case_id, "code": "namespace_unavailable"}
            )
            continue
        raw_results = search_chunks(
            case.question,
            top_k=settings.agent_top_k,
            source_namespace=probe.namespace,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            state_store=state_store,
        )
        response = tool.execute(
            ScopedSearchRequest(
                query=case.question,
                top_k=settings.agent_top_k,
            )
        )
        probe_result, probe_failures = _evaluate_probe(
            case,
            probe,
            response=response,
            raw_results=raw_results,
            indexed_by_chunk=indexed_by_chunk,
            context_max_characters=settings.rag_context_max_characters,
        )
        probe_results.append(probe_result)
        failures.extend(probe_failures)

    route_namespace = {
        AgentIntent.KNOWLEDGE_QUESTION: "notes",
        AgentIntent.PROJECT_CONTEXT: "projects",
        AgentIntent.RESUME_CONTEXT: "resume",
    }.get(case.expected_intent)
    routed_probe = next(
        (
            probe
            for probe in case.probes
            if probe.namespace == route_namespace
        ),
        None,
    )
    agent_checked = routed_probe is not None and route_namespace in tools
    agent_passed: bool | None = None
    llm_calls_before = len(llm.calls)
    if agent_checked and routed_probe is not None:
        response = agent.execute(AgentRequest(question=case.question))
        llm_call_delta = len(llm.calls) - llm_calls_before
        if routed_probe.expectation is ProbeExpectation.SUCCESS:
            agent_passed = (
                response.status is AgentStatus.SUCCESS
                and bool(response.citations)
                and llm_call_delta == 1
            )
        else:
            agent_passed = (
                response.status is AgentStatus.NO_EVIDENCE
                and not response.citations
                and llm_call_delta == 0
            )
        if not agent_passed:
            failure_code = (
                "agent_positive_boundary_failed"
                if routed_probe.expectation is ProbeExpectation.SUCCESS
                else "agent_refusal_boundary_failed"
            )
            failures.append(
                {"case_id": case.case_id, "code": failure_code}
            )

    return {
        "case_id": case.case_id,
        "route_passed": route_passed,
        "agent_checked": agent_checked,
        "agent_expectation": (
            routed_probe.expectation.value if routed_probe is not None else None
        ),
        "agent_passed": agent_passed,
        "probes": probe_results,
        "failures": failures,
    }


def _evaluate_probe(
    case: AcceptanceCase,
    probe: AcceptanceProbe,
    *,
    response: ScopedSearchResponse,
    raw_results: Sequence[Any],
    indexed_by_chunk: Mapping[str, Any],
    context_max_characters: int,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """验证单个 namespace 的状态、命中名次、引用和脱敏。"""
    failures: list[dict[str, str]] = []
    expected_status = (
        ScopedSearchStatus.SUCCESS
        if probe.expectation is ProbeExpectation.SUCCESS
        else ScopedSearchStatus.NO_RESULTS
    )
    status_passed = response.status is expected_status
    namespace_passed = all(
        result.source_namespace == probe.namespace for result in response.results
    )
    if not namespace_passed:
        failures.append(
            {"case_id": case.case_id, "code": "namespace_mismatch"}
        )

    expected_paths = set(probe.expected_paths)
    hit_rank = next(
        (
            result.rank
            for result in response.results
            if result.relative_path.replace("\\", "/") in expected_paths
        ),
        None,
    )
    raw_hit_rank = next(
        (
            index
            for index, result in enumerate(raw_results, start=1)
            if result.relative_path.as_posix() in expected_paths
        ),
        None,
    )
    hit_passed = (
        hit_rank is not None
        if probe.expectation is ProbeExpectation.SUCCESS
        else not response.results
    )
    if not status_passed or not hit_passed:
        failures.append(
            {"case_id": case.case_id, "code": "retrieval_expectation_failed"}
        )

    citation_passed = probe.expectation is ProbeExpectation.NO_RESULTS
    redaction_passed = True
    if response.status is ScopedSearchStatus.SUCCESS:
        citation_passed = _verify_citations(
            response,
            indexed_by_chunk=indexed_by_chunk,
            context_max_characters=context_max_characters,
        )
        if not citation_passed:
            failures.append(
                {"case_id": case.case_id, "code": "citation_invalid"}
            )
        if probe.namespace == "resume":
            redaction_passed = all(
                redact_common_personal_data(result.content) == result.content
                for result in response.results
            )
            if not redaction_passed:
                failures.append(
                    {
                        "case_id": case.case_id,
                        "code": "resume_redaction_failed",
                    }
                )

    return (
        {
            "namespace": probe.namespace,
            "category": probe.category.value,
            "critical": probe.critical,
            "status": response.status.value,
            "passed": (
                status_passed
                and namespace_passed
                and hit_passed
                and citation_passed
                and redaction_passed
            ),
            "hit_rank": hit_rank,
            "raw_hit_rank": raw_hit_rank,
            "raw_top_score": (
                round(float(raw_results[0].score), 6) if raw_results else None
            ),
            "returned_count": len(response.results),
            "duration_ms": response.duration_ms,
            "citation_passed": citation_passed,
            "redaction_passed": redaction_passed,
        },
        failures,
    )


def _verify_citations(
    response: ScopedSearchResponse,
    *,
    indexed_by_chunk: Mapping[str, Any],
    context_max_characters: int,
) -> bool:
    """确保每条 Tool 证据和 RAG 引用都映射回本次真实片段。"""
    try:
        context = build_scoped_search_context(
            response,
            expected_tool_name=response.tool_name,
            max_characters=context_max_characters,
        )
    except Exception:
        return False
    if context.status is not RagContextStatus.READY or not context.citations:
        return False
    for result in response.results:
        chunk = indexed_by_chunk.get(result.chunk_id)
        if chunk is None:
            return False
        if (
            chunk.document_id != result.document_id
            or chunk.source_namespace != result.source_namespace
            or chunk.relative_path.as_posix()
            != result.relative_path.replace("\\", "/")
            or chunk.start_line != result.start_line
            or chunk.end_line != result.end_line
            or chunk.fingerprint != result.fingerprint
        ):
            return False
    citation_chunk_ids = {citation.chunk_id for citation in context.citations}
    result_chunk_ids = {result.chunk_id for result in response.results}
    return citation_chunk_ids <= result_chunk_ids


def _load_index_documents(
    settings: Settings,
    *,
    source_paths: Mapping[str, Path],
) -> tuple[tuple[IndexDocument, ...], dict[str, dict[str, int]]]:
    """稳定加载现有 namespace，并实施三源合计字节上限。"""
    prepared: list[IndexDocument] = []
    stats: dict[str, dict[str, int]] = {}
    total_size_bytes = 0
    for namespace in _REQUIRED_NAMESPACES:
        source = source_paths.get(namespace)
        if source is None:
            continue
        loaded = load_markdown_documents(
            source,
            tuple(source_paths.values()),
            max_file_size_bytes=settings.markdown_max_file_size_bytes,
            max_total_size_bytes=settings.markdown_max_total_size_bytes,
        )
        if not loaded:
            raise VaultAcceptanceError(
                "Every configured acceptance source must contain Markdown."
            )
        source_size = sum(
            len(document.content.encode("utf-8")) for document in loaded
        )
        total_size_bytes += source_size
        if total_size_bytes > settings.markdown_max_total_size_bytes:
            raise VaultAcceptanceError(
                "Combined acceptance sources exceed the configured byte limit."
            )
        indexed = prepare_index_documents(
            loaded,
            max_chunk_characters=settings.markdown_chunk_max_characters,
            source_namespace=namespace,
        )
        prepared.extend(indexed)
        stats[namespace] = {
            "document_count": len(indexed),
            "chunk_count": sum(len(document.chunks) for document in indexed),
            "utf8_bytes": source_size,
        }
    return tuple(prepared), stats


def _build_tools(
    settings: Settings,
    *,
    embedding_provider: EmbeddingProvider,
    vector_store: ChromaVectorStore,
    state_store: SQLiteIndexStateStore,
    trace_store: SQLiteToolTraceStore,
) -> dict[str, Any]:
    """只为实际加载的 namespace 构造固定边界 Tool。"""
    common = {
        "embedding_provider": embedding_provider,
        "vector_store": vector_store,
        "state_store": state_store,
        "trace_store": trace_store,
    }
    return {
        "notes": SearchNotesTool(
            **common,
            min_score=settings.search_notes_min_score,
            max_total_characters=settings.search_notes_max_total_characters,
        ),
        "projects": GetProjectContextTool(
            **common,
            min_score=settings.project_context_min_score,
            max_total_characters=settings.project_context_max_total_characters,
        ),
        "resume": GetResumeContextTool(
            **common,
            min_score=settings.resume_context_min_score,
            max_total_characters=settings.resume_context_max_total_characters,
        ),
    }


def _resolve_source_paths(
    settings: Settings,
    *,
    allow_incomplete_sources: bool,
) -> tuple[dict[str, Path], tuple[str, ...]]:
    """解析真实路径，正式模式拒绝缺失源，预检模式明确标记缺口。"""
    configured = {
        "notes": settings.markdown_source_directory,
        "projects": settings.project_source_directory,
        "resume": settings.resume_source_directory,
    }
    resolved: dict[str, Path] = {}
    missing: list[str] = []
    for namespace, configured_path in configured.items():
        try:
            path = configured_path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            missing.append(namespace)
            continue
        if not path.is_dir():
            missing.append(namespace)
            continue
        resolved[namespace] = path
    if missing and not allow_incomplete_sources:
        raise VaultAcceptanceError(
            "Formal Vault acceptance requires all three source directories."
        )
    if not resolved:
        raise VaultAcceptanceError("No acceptance source directory is available.")
    paths = tuple(resolved.values())
    allowed = _resolve_existing_allowed_directories(
        settings.allowed_data_directories,
        allow_incomplete_sources=allow_incomplete_sources,
    )
    for source in paths:
        if not any(
            source == allowed_path or allowed_path in source.parents
            for allowed_path in allowed
        ):
            raise VaultAcceptanceError(
                "An acceptance source is outside ALLOWED_DATA_DIRECTORIES."
            )
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise VaultAcceptanceError(
                    "Acceptance source directories must not overlap."
                )
    return resolved, tuple(missing)


def _resolve_existing_allowed_directories(
    configured: Sequence[Path],
    *,
    allow_incomplete_sources: bool,
) -> tuple[Path, ...]:
    """正式模式要求白名单全部存在，预检只忽略尚未准备的源目录。"""
    resolved: list[Path] = []
    for path in configured:
        try:
            candidate = path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            if allow_incomplete_sources:
                continue
            raise VaultAcceptanceError(
                "An allowed data directory cannot be resolved."
            ) from None
        if not candidate.is_dir():
            raise VaultAcceptanceError(
                "An allowed data path is not a directory."
            )
        resolved.append(candidate)
    if not resolved:
        raise VaultAcceptanceError(
            "No configured allowed data directory is available."
        )
    return tuple(resolved)


def _validate_runtime_boundaries(
    settings: Settings,
    source_paths: Mapping[str, Path],
    report_path: Path,
) -> None:
    """拒绝把 SQLite、Chroma、缓存或报告写入任一 Vault 数据源。"""
    runtime_paths = (
        settings.database_path.expanduser().resolve(strict=False),
        settings.vector_store_path.expanduser().resolve(strict=False),
        settings.embedding_cache_directory.expanduser().resolve(strict=False),
        report_path,
    )
    for runtime_path in runtime_paths:
        for source_path in source_paths.values():
            if runtime_path == source_path or source_path in runtime_path.parents:
                raise VaultAcceptanceError(
                    "Acceptance runtime data must stay outside Vault sources."
                )


def _validate_case_coverage(
    cases: Sequence[AcceptanceCase],
    *,
    loaded_namespaces: tuple[str, ...],
    formal_complete: bool,
) -> None:
    """问题集只能探测已加载源，正式模式必须覆盖三个正例 namespace。"""
    loaded = set(loaded_namespaces)
    positive_namespaces: set[str] = set()
    negative_count = 0
    for case in cases:
        for probe in case.probes:
            if probe.namespace not in loaded:
                raise VaultAcceptanceError(
                    "Acceptance cases reference an unavailable namespace."
                )
            if probe.category is ProbeCategory.POSITIVE:
                positive_namespaces.add(probe.namespace)
            else:
                negative_count += 1
    if not set(loaded_namespaces) <= positive_namespaces:
        raise VaultAcceptanceError(
            "Every loaded namespace requires at least one positive case."
        )
    if negative_count == 0:
        raise VaultAcceptanceError(
            "Acceptance cases require at least one adversarial negative probe."
        )
    if formal_complete and positive_namespaces != set(_REQUIRED_NAMESPACES):
        raise VaultAcceptanceError(
            "Formal acceptance requires positive coverage for all namespaces."
        )


def _snapshot_sources(
    source_paths: Mapping[str, Path],
) -> tuple[_SourceSnapshotEntry, ...]:
    """记录所有目录项，文件额外保存 SHA-256，证明验收过程零修改。"""
    entries: list[_SourceSnapshotEntry] = []
    for namespace in _REQUIRED_NAMESPACES:
        source = source_paths.get(namespace)
        if source is None:
            continue
        for path in sorted(
            source.rglob("*"),
            key=lambda item: item.relative_to(source).as_posix().casefold(),
        ):
            try:
                stat = path.lstat()
            except OSError as error:
                raise VaultAcceptanceError(
                    "A Vault source entry could not be inspected."
                ) from error
            if path.is_symlink() or getattr(stat, "st_file_attributes", 0) & 1024:
                raise VaultAcceptanceError(
                    "Vault acceptance rejects symbolic links and reparse points."
                )
            if path.is_dir():
                entry_type = "directory"
                content_hash = None
                size_bytes = 0
            elif path.is_file():
                entry_type = "file"
                size_bytes = stat.st_size
                # 加载器只读取 Markdown；其他文件只比较大小和修改时间，避免无界读取。
                if path.suffix.casefold() == ".md":
                    try:
                        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                    except OSError as error:
                        raise VaultAcceptanceError(
                            "A Vault source file could not be hashed."
                        ) from error
                else:
                    content_hash = None
            else:
                raise VaultAcceptanceError(
                    "Vault acceptance encountered an unsupported entry type."
                )
            entries.append(
                _SourceSnapshotEntry(
                    namespace=namespace,
                    relative_path=path.relative_to(source).as_posix(),
                    entry_type=entry_type,
                    size_bytes=size_bytes,
                    modified_time_ns=stat.st_mtime_ns,
                    content_sha256=content_hash,
                )
            )
    return tuple(entries)


def _snapshot_fingerprint(
    entries: Sequence[_SourceSnapshotEntry],
) -> str:
    """只在报告中写入整体指纹，不暴露文件路径。"""
    payload = [
        {
            "namespace": entry.namespace,
            "relative_path": entry.relative_path,
            "entry_type": entry.entry_type,
            "size_bytes": entry.size_bytes,
            "modified_time_ns": entry.modified_time_ns,
            "content_sha256": entry.content_sha256,
        }
        for entry in entries
    ]
    return _sha256_json(payload)


def _verify_database_boundary(
    database_path: Path,
    *,
    cases: Sequence[AcceptanceCase],
    source_paths: tuple[Path, ...],
) -> bool:
    """扫描 SQLite 文本列，确保没有问题正文和绝对 Vault 路径。"""
    forbidden = [case.question for case in cases]
    forbidden.extend(str(path) for path in source_paths)
    forbidden.extend(path.as_posix() for path in source_paths)
    try:
        connection = sqlite3.connect(database_path)
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        for (table_name,) in tables:
            if not isinstance(table_name, str) or table_name.startswith("sqlite_"):
                continue
            columns = connection.execute(
                f'PRAGMA table_info("{table_name.replace(chr(34), chr(34) * 2)}")'
            ).fetchall()
            text_columns = [
                row[1]
                for row in columns
                if isinstance(row[2], str) and "TEXT" in row[2].upper()
            ]
            for column in text_columns:
                escaped_table = table_name.replace('"', '""')
                escaped_column = str(column).replace('"', '""')
                rows = connection.execute(
                    f'SELECT "{escaped_column}" FROM "{escaped_table}" '
                    f'WHERE "{escaped_column}" IS NOT NULL'
                ).fetchall()
                for (value,) in rows:
                    if isinstance(value, str) and any(
                        secret and secret in value for secret in forbidden
                    ):
                        return False
    except sqlite3.Error:
        return False
    finally:
        if "connection" in locals():
            connection.close()
    return True


def _verify_llm_payloads(calls: Sequence[tuple[Any, ...]]) -> bool:
    """确定性替身收到的内容不得再含受支持的常见个人数据。"""
    for messages in calls:
        for message in messages:
            content = getattr(message, "content", None)
            if not isinstance(content, str):
                return False
            if redact_common_personal_data(content) != content:
                return False
    return True


def _calculate_metrics(case_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """从匿名结果计算路由、召回、拒答、引用和 Agent 指标。"""
    route_total = len(case_results)
    route_passed = sum(bool(result["route_passed"]) for result in case_results)
    probes = [probe for result in case_results for probe in result["probes"]]
    positives = [
        probe
        for probe in probes
        if probe["category"] == ProbeCategory.POSITIVE.value
    ]
    hard_negatives = [
        probe
        for probe in probes
        if probe["category"] == ProbeCategory.HARD_NEGATIVE.value
    ]
    cross_namespace = [
        probe
        for probe in probes
        if probe["category"] == ProbeCategory.CROSS_NAMESPACE.value
    ]
    hit_at_1 = sum(probe["hit_rank"] == 1 for probe in positives)
    hit_at_5 = sum(
        isinstance(probe["hit_rank"], int) and probe["hit_rank"] <= 5
        for probe in positives
    )
    reciprocal_rank = sum(
        1.0 / probe["hit_rank"]
        for probe in positives
        if isinstance(probe["hit_rank"], int)
    )
    checked_agents = [result for result in case_results if result["agent_checked"]]
    positive_agents = [
        result
        for result in checked_agents
        if result["agent_expectation"] == ProbeExpectation.SUCCESS.value
    ]
    refusal_agents = [
        result
        for result in checked_agents
        if result["agent_expectation"] == ProbeExpectation.NO_RESULTS.value
    ]
    successful_positives = [
        probe for probe in positives if probe["status"] == "success"
    ]
    return {
        "route_total": route_total,
        "route_accuracy": _ratio(route_passed, route_total),
        "positive_total": len(positives),
        "hit_at_1": _ratio(hit_at_1, len(positives)),
        "hit_at_5": _ratio(hit_at_5, len(positives)),
        "mrr": round(reciprocal_rank / len(positives), 6) if positives else 0.0,
        "hard_negative_total": len(hard_negatives),
        "hard_negative_rejection_rate": _ratio(
            sum(probe["passed"] for probe in hard_negatives),
            len(hard_negatives),
        ),
        "cross_namespace_total": len(cross_namespace),
        "cross_namespace_rejection_rate": _ratio(
            sum(probe["passed"] for probe in cross_namespace),
            len(cross_namespace),
        ),
        "positive_grounding_rate": _ratio(
            sum(probe["citation_passed"] for probe in positives),
            len(positives),
        ),
        "citation_integrity_rate": _ratio(
            sum(probe["citation_passed"] for probe in successful_positives),
            len(successful_positives),
        ),
        "agent_boundary_total": len(checked_agents),
        "agent_boundary_pass_rate": _ratio(
            sum(result["agent_passed"] is True for result in checked_agents),
            len(checked_agents),
        ),
        "agent_positive_pass_rate": _ratio(
            sum(result["agent_passed"] is True for result in positive_agents),
            len(positive_agents),
        ),
        "agent_refusal_pass_rate": _ratio(
            sum(result["agent_passed"] is True for result in refusal_agents),
            len(refusal_agents),
        ),
    }


def _phase2_triggers(
    metrics: Mapping[str, Any],
    case_results: Sequence[Mapping[str, Any]],
) -> list[str]:
    """只记录进入 Phase 2 的证据，不在 v0.3.x 中实施优化。"""
    triggers: list[str] = []
    if metrics["hit_at_5"] < 0.9:
        triggers.append("positive_hit_at_5_below_0_90")
    if metrics["hit_at_1"] < 0.7:
        triggers.append("positive_hit_at_1_below_0_70")
    if metrics["mrr"] < 0.75:
        triggers.append("positive_mrr_below_0_75")
    if metrics["hard_negative_rejection_rate"] < 0.95:
        triggers.append("hard_negative_rejection_below_0_95")
    if metrics["cross_namespace_rejection_rate"] < 1.0:
        triggers.append("cross_namespace_rejection_below_1_00")
    if any(
        probe["category"] == ProbeCategory.POSITIVE.value
        and probe["critical"]
        and not probe["passed"]
        for result in case_results
        for probe in result["probes"]
    ):
        triggers.append("critical_positive_failed")
    return triggers


def _write_sanitized_report(
    path: Path,
    report: Mapping[str, Any],
    *,
    cases: Sequence[AcceptanceCase],
    source_paths: tuple[Path, ...],
) -> None:
    """原子写入匿名报告，并在落盘前检查未携带原始问题和路径。"""
    serialized = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    forbidden = [case.question for case in cases]
    forbidden.extend(str(path) for path in source_paths)
    forbidden.extend(path.as_posix() for path in source_paths)
    if any(secret and secret in serialized for secret in forbidden):
        raise VaultAcceptanceError(
            "The anonymized report unexpectedly contains sensitive input."
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        temporary.write_text(serialized + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        raise VaultAcceptanceError(
            "The anonymized acceptance report could not be written."
        ) from error


def _parse_case_payload(payload: object) -> tuple[AcceptanceCase, ...]:
    """把未知 JSON 严格转换为稳定问题集。"""
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "cases",
    }:
        raise ValueError("invalid root object")
    if payload["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("unsupported schema version")
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= _MAX_CASES:
        raise ValueError("invalid case count")
    cases: list[AcceptanceCase] = []
    seen_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict) or set(raw_case) != {
            "case_id",
            "question",
            "expected_intent",
            "probes",
        }:
            raise ValueError("invalid case object")
        case_id = raw_case["case_id"]
        question = raw_case["question"]
        if not isinstance(case_id, str) or not _CASE_ID_PATTERN.fullmatch(case_id):
            raise ValueError("invalid case id")
        if case_id in seen_ids:
            raise ValueError("duplicate case id")
        seen_ids.add(case_id)
        if (
            not isinstance(question, str)
            or not question.strip()
            or len(question) > _MAX_QUESTION_CHARACTERS
            or _contains_control_character(question)
        ):
            raise ValueError("invalid question")
        expected_intent = AgentIntent(raw_case["expected_intent"])
        raw_probes = raw_case["probes"]
        if (
            not isinstance(raw_probes, list)
            or len(raw_probes) > _MAX_PROBES_PER_CASE
        ):
            raise ValueError("invalid probes")
        probes = tuple(_parse_probe(probe) for probe in raw_probes)
        if len({probe.namespace for probe in probes}) != len(probes):
            raise ValueError("duplicate probe namespace")
        cases.append(
            AcceptanceCase(
                case_id=case_id,
                question=question.strip(),
                expected_intent=expected_intent,
                probes=probes,
            )
        )
    return tuple(cases)


def _parse_probe(payload: object) -> AcceptanceProbe:
    """严格校验单个检索探针和期望路径。"""
    if not isinstance(payload, dict) or set(payload) != {
        "namespace",
        "category",
        "expectation",
        "expected_paths",
        "critical",
    }:
        raise ValueError("invalid probe object")
    namespace = payload["namespace"]
    if namespace not in _REQUIRED_NAMESPACES:
        raise ValueError("invalid namespace")
    category = ProbeCategory(payload["category"])
    expectation = ProbeExpectation(payload["expectation"])
    critical = payload["critical"]
    if not isinstance(critical, bool):
        raise ValueError("invalid critical flag")
    raw_paths = payload["expected_paths"]
    if not isinstance(raw_paths, list):
        raise ValueError("invalid expected paths")
    expected_paths = tuple(_normalize_relative_path(path) for path in raw_paths)
    if len(set(expected_paths)) != len(expected_paths):
        raise ValueError("duplicate expected paths")
    if expectation is ProbeExpectation.SUCCESS:
        if category is not ProbeCategory.POSITIVE or not expected_paths:
            raise ValueError("success probes require positive expected paths")
    elif category is ProbeCategory.POSITIVE or expected_paths:
        raise ValueError("negative probes cannot contain expected paths")
    return AcceptanceProbe(
        namespace=namespace,
        category=category,
        expectation=expectation,
        expected_paths=expected_paths,
        critical=critical,
    )


def _normalize_relative_path(value: object) -> str:
    """问题集只允许不含父级跳转的 POSIX 相对路径。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid relative path")
    normalized = value.replace("\\", "/").strip()
    candidate = Path(normalized)
    if (
        candidate.is_absolute()
        or candidate.drive
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or not _SAFE_RELATIVE_PATH_PATTERN.fullmatch(normalized)
    ):
        raise ValueError("unsafe relative path")
    return normalized


def _case_manifest_fingerprint(cases: Sequence[AcceptanceCase]) -> str:
    """对本地问题集完整内容取指纹，报告本身不泄露正文。"""
    payload = [
        {
            "case_id": case.case_id,
            "question": case.question,
            "expected_intent": case.expected_intent.value,
            "probes": [
                {
                    "namespace": probe.namespace,
                    "category": probe.category.value,
                    "expectation": probe.expectation.value,
                    "expected_paths": probe.expected_paths,
                    "critical": probe.critical,
                }
                for probe in case.probes
            ],
        }
        for case in cases
    ]
    return _sha256_json(payload)


def _configuration_fingerprint(settings: Settings) -> str:
    """对非秘密配置取指纹，不把本机路径写入报告。"""
    payload = {
        "sources": {
            "notes": str(settings.markdown_source_directory.resolve(False)),
            "projects": str(settings.project_source_directory.resolve(False)),
            "resume": str(settings.resume_source_directory.resolve(False)),
        },
        "allowed": [
            str(path.resolve(False)) for path in settings.allowed_data_directories
        ],
        "chunk_max_characters": settings.markdown_chunk_max_characters,
        "embedding_model": settings.embedding_model_name,
        "embedding_batch_size": settings.embedding_batch_size,
        "top_k": settings.agent_top_k,
        "thresholds": {
            "notes": settings.search_notes_min_score,
            "projects": settings.project_context_min_score,
            "resume": settings.resume_context_min_score,
        },
    }
    return _sha256_json(payload)


def _package_version() -> str:
    """开发安装缺失元数据时返回稳定占位值。"""
    try:
        return version("interview-agent")
    except PackageNotFoundError:
        return "unknown"


def _ratio(numerator: int, denominator: int) -> float:
    """以六位小数记录小规模固定集比例。"""
    return round(numerator / denominator, 6) if denominator else 0.0


def _sha256_json(value: object) -> str:
    """生成顺序稳定的 UTF-8 JSON 指纹。"""
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _contains_control_character(value: str) -> bool:
    """问题只允许换行和制表符之外的普通 UTF-8 文本。"""
    return any(
        ord(character) < 32 and character not in "\t\r\n"
        or ord(character) == 127
        for character in value
    )
