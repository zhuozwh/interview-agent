"""验证真实 Vault 验收入口本身的隔离、匿名和对抗边界。"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.acceptance import (
    VaultAcceptanceError,
    load_acceptance_cases,
    run_real_vault_acceptance,
)
from interview_agent.core.config import Settings


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """每个验收测试独占源目录和运行时目录。"""
    with TemporaryDirectory(
        prefix="interview-agent-vault-acceptance-test-"
    ) as directory:
        yield Path(directory)


class AcceptanceEmbedding:
    """按三个 namespace 主题和库外主题生成正交确定向量。"""

    model_name = "acceptance-test-embedding-v1"
    dimension = 4

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self._vector(query)

    @staticmethod
    def _vector(text: str) -> list[float]:
        if "智能指针" in text or "RAII" in text:
            return [1.0, 0.0, 0.0, 0.0]
        if "Reactor" in text or "事件循环" in text:
            return [0.0, 1.0, 0.0, 0.0]
        if "实习" in text or "后端经历" in text:
            return [0.0, 0.0, 1.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0]


def test_real_vault_acceptance_runs_three_sources_without_sensitive_report(
    temporary_directory: Path,
) -> None:
    """三源正例、硬负例、跨域探针、引用和脱敏必须同时通过。"""
    settings, cases_path, report_path = _build_complete_fixture(
        temporary_directory
    )
    source_files = tuple(
        path
        for path in (temporary_directory / "vault").rglob("*.md")
    )
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in source_files
    }

    report = run_real_vault_acceptance(
        settings,
        load_acceptance_cases(cases_path),
        report_path=report_path,
        embedding_provider=AcceptanceEmbedding(),
    )

    assert report["formal_complete"] is True
    assert report["acceptance_passed"] is True
    assert report["safety_passed"] is True
    assert report["quality_gates_passed"] is True
    assert report["vault_unchanged"] is True
    assert report["second_sync_idempotent"] is True
    assert report["database_boundary_passed"] is True
    assert report["privacy_boundary_passed"] is True
    assert report["metrics"]["route_accuracy"] == 1.0
    assert report["metrics"]["hit_at_1"] == 1.0
    assert report["metrics"]["hit_at_5"] == 1.0
    assert report["metrics"]["mrr"] == 1.0
    assert report["metrics"]["hard_negative_rejection_rate"] == 1.0
    assert report["metrics"]["cross_namespace_rejection_rate"] == 1.0
    assert report["metrics"]["positive_grounding_rate"] == 1.0
    assert report["metrics"]["citation_integrity_rate"] == 1.0
    assert report["metrics"]["agent_boundary_pass_rate"] == 1.0
    assert report["metrics"]["agent_positive_pass_rate"] == 1.0
    assert report["metrics"]["agent_refusal_pass_rate"] == 1.0
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in source_files
    } == before

    serialized = report_path.read_text(encoding="utf-8")
    for forbidden in (
        "智能指针如何体现 RAII",
        "我的项目中 Reactor",
        "我的简历里有哪些后端实习经历",
        "memory.md",
        "server.md",
        "resume.md",
        "candidate@example.com",
        "13812345678",
        "file:///E:/private/resume/candidate.docx",
        str(temporary_directory / "vault"),
    ):
        assert forbidden not in serialized


def test_real_vault_acceptance_prebaseline_marks_missing_resume(
    temporary_directory: Path,
) -> None:
    """缺少简历时可运行双源预基线，但绝不能伪装成正式通过。"""
    settings, cases_path, report_path = _build_incomplete_fixture(
        temporary_directory
    )

    report = run_real_vault_acceptance(
        settings,
        load_acceptance_cases(cases_path),
        report_path=report_path,
        allow_incomplete_sources=True,
        embedding_provider=AcceptanceEmbedding(),
    )

    assert report["formal_complete"] is False
    assert report["missing_namespaces"] == ["resume"]
    assert report["safety_passed"] is True
    assert report["quality_gates_passed"] is True
    assert report["acceptance_passed"] is False


def test_formal_acceptance_rejects_missing_resume_before_runtime_write(
    temporary_directory: Path,
) -> None:
    """正式模式缺源时应在创建 SQLite 和 Chroma 前停止。"""
    settings, cases_path, report_path = _build_incomplete_fixture(
        temporary_directory
    )

    with pytest.raises(VaultAcceptanceError, match="all three"):
        run_real_vault_acceptance(
            settings,
            load_acceptance_cases(cases_path),
            report_path=report_path,
            embedding_provider=AcceptanceEmbedding(),
        )

    assert not settings.database_path.exists()
    assert not settings.vector_store_path.exists()
    assert not report_path.exists()


def test_acceptance_rejects_runtime_path_inside_vault_source(
    temporary_directory: Path,
) -> None:
    """运行时数据不能因错误配置写进任何只读数据源。"""
    settings, cases_path, report_path = _build_complete_fixture(
        temporary_directory
    )
    unsafe_settings = Settings(
        _env_file=None,
        **{
            **_settings_values(settings),
            "database_path": settings.markdown_source_directory / "state.sqlite3",
        },
    )

    with pytest.raises(VaultAcceptanceError, match="outside Vault"):
        run_real_vault_acceptance(
            unsafe_settings,
            load_acceptance_cases(cases_path),
            report_path=report_path,
            embedding_provider=AcceptanceEmbedding(),
        )

    assert not unsafe_settings.database_path.exists()


def test_acceptance_rejects_llm_key_and_nonlocal_embedding(
    temporary_directory: Path,
) -> None:
    """离线验收即使不会构造客户端，也拒绝容易误用的远端配置。"""
    settings, cases_path, report_path = _build_complete_fixture(
        temporary_directory
    )
    cases = load_acceptance_cases(cases_path)
    with_key = Settings(
        _env_file=None,
        **{**_settings_values(settings), "llm_api_key": "secret-test-key"},
    )
    with pytest.raises(VaultAcceptanceError, match="LLM_API_KEY"):
        run_real_vault_acceptance(
            with_key,
            cases,
            report_path=report_path,
            embedding_provider=AcceptanceEmbedding(),
        )

    online_embedding = Settings(
        _env_file=None,
        **{
            **_settings_values(settings),
            "embedding_local_files_only": False,
        },
    )
    with pytest.raises(VaultAcceptanceError, match="LOCAL_FILES_ONLY"):
        run_real_vault_acceptance(
            online_embedding,
            cases,
            report_path=report_path,
            embedding_provider=AcceptanceEmbedding(),
        )


def test_critical_retrieval_miss_becomes_phase2_trigger(
    temporary_directory: Path,
) -> None:
    """质量失败只记录为 Phase 2 证据，不在验收器里静默调参。"""
    settings, cases_path, report_path = _build_complete_fixture(
        temporary_directory
    )
    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    payload["cases"][0]["probes"][0]["expected_paths"] = ["missing.md"]
    cases_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    report = run_real_vault_acceptance(
        settings,
        load_acceptance_cases(cases_path),
        report_path=report_path,
        embedding_provider=AcceptanceEmbedding(),
    )

    assert report["safety_passed"] is True
    assert report["quality_gates_passed"] is False
    assert report["acceptance_passed"] is False
    assert "critical_positive_failed" in report["phase2_triggers"]
    assert {
        failure["code"] for failure in report["failures"]
    } == {"retrieval_expectation_failed"}


@pytest.mark.parametrize(
    "mutation",
    (
        "absolute_path",
        "parent_traversal",
        "duplicate_case",
        "unknown_field",
        "success_without_path",
        "control_character",
    ),
)
def test_case_file_rejects_adversarial_schema(
    temporary_directory: Path,
    mutation: str,
) -> None:
    """本地私人问题集也不能绕过路径、数量和结构边界。"""
    path = temporary_directory / "cases.json"
    payload = _complete_case_payload()
    if mutation == "absolute_path":
        payload["cases"][0]["probes"][0]["expected_paths"] = [
            "C:/private.md"
        ]
    elif mutation == "parent_traversal":
        payload["cases"][0]["probes"][0]["expected_paths"] = ["../private.md"]
    elif mutation == "duplicate_case":
        payload["cases"].append(dict(payload["cases"][0]))
    elif mutation == "unknown_field":
        payload["cases"][0]["unexpected"] = "value"
    elif mutation == "success_without_path":
        payload["cases"][0]["probes"][0]["expected_paths"] = []
    elif mutation == "control_character":
        payload["cases"][0]["question"] = "unsafe\u0000question"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(VaultAcceptanceError, match="schema version 1"):
        load_acceptance_cases(path)


def _build_complete_fixture(
    root: Path,
) -> tuple[Settings, Path, Path]:
    """创建三个互斥测试源和隔离运行时配置。"""
    vault = root / "vault"
    notes = vault / "notes"
    projects = vault / "projects"
    resume = vault / "resume"
    notes.mkdir(parents=True)
    projects.mkdir()
    resume.mkdir()
    (notes / "memory.md").write_text(
        "# 智能指针\nRAII 通过对象生命周期管理资源。",
        encoding="utf-8",
    )
    (projects / "server.md").write_text(
        "# 事件循环\n当前服务框架采用 Reactor。",
        encoding="utf-8",
    )
    (resume / "resume.md").write_text(
        "# 后端经历\n实习期间实现服务模块。\n"
        "邮箱 candidate@example.com\n手机 13812345678\n"
        "原件 file:///E:/private/resume/candidate.docx",
        encoding="utf-8",
    )
    runtime = root / "runtime"
    settings = Settings(
        _env_file=None,
        markdown_source_directory=notes,
        project_source_directory=projects,
        resume_source_directory=resume,
        allowed_data_directories=(notes, projects, resume),
        database_path=runtime / "state.sqlite3",
        vector_store_path=runtime / "vector",
        vector_collection_name="vault_acceptance_test",
        embedding_cache_directory=runtime / "cache",
        embedding_local_files_only=True,
        search_notes_min_score=0.5,
        project_context_min_score=0.5,
        resume_context_min_score=0.5,
        agent_top_k=3,
    )
    cases_path = root / "cases.json"
    cases_path.write_text(
        json.dumps(_complete_case_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    return settings, cases_path, runtime / "report.json"


def _build_incomplete_fixture(
    root: Path,
) -> tuple[Settings, Path, Path]:
    """创建缺少 resume 的双源预基线配置。"""
    settings, cases_path, report_path = _build_complete_fixture(root)
    resume_file = settings.resume_source_directory / "resume.md"
    resume_file.unlink()
    settings.resume_source_directory.rmdir()
    payload = _complete_case_payload()
    payload["cases"] = [
        case for case in payload["cases"] if case["case_id"] != "R01"
    ]
    cases_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return settings, cases_path, report_path


def _complete_case_payload() -> dict:
    """返回同时覆盖正例、硬负例和跨 namespace 的固定协议。"""
    return {
        "schema_version": 1,
        "cases": [
            {
                "case_id": "N01",
                "question": "智能指针如何体现 RAII？",
                "expected_intent": "knowledge_question",
                "probes": [
                    {
                        "namespace": "notes",
                        "category": "positive",
                        "expectation": "success",
                        "expected_paths": ["memory.md"],
                        "critical": True,
                    },
                    {
                        "namespace": "projects",
                        "category": "cross_namespace",
                        "expectation": "no_results",
                        "expected_paths": [],
                        "critical": False,
                    },
                ],
            },
            {
                "case_id": "P01",
                "question": "我的项目中 Reactor 当前实现是什么？",
                "expected_intent": "project_context",
                "probes": [
                    {
                        "namespace": "projects",
                        "category": "positive",
                        "expectation": "success",
                        "expected_paths": ["server.md"],
                        "critical": True,
                    }
                ],
            },
            {
                "case_id": "R01",
                "question": "我的简历里有哪些后端实习经历？",
                "expected_intent": "resume_context",
                "probes": [
                    {
                        "namespace": "resume",
                        "category": "positive",
                        "expectation": "success",
                        "expected_paths": ["resume.md"],
                        "critical": True,
                    }
                ],
            },
            {
                "case_id": "HN01",
                "question": "库外主题如何实现？",
                "expected_intent": "knowledge_question",
                "probes": [
                    {
                        "namespace": "notes",
                        "category": "hard_negative",
                        "expectation": "no_results",
                        "expected_paths": [],
                        "critical": False,
                    }
                ],
            },
        ],
    }


def _settings_values(settings: Settings) -> dict:
    """复制与验收有关的配置，便于只替换一个对抗字段。"""
    return {
        "markdown_source_directory": settings.markdown_source_directory,
        "project_source_directory": settings.project_source_directory,
        "resume_source_directory": settings.resume_source_directory,
        "allowed_data_directories": settings.allowed_data_directories,
        "database_path": settings.database_path,
        "vector_store_path": settings.vector_store_path,
        "vector_collection_name": settings.vector_collection_name,
        "embedding_cache_directory": settings.embedding_cache_directory,
        "embedding_local_files_only": settings.embedding_local_files_only,
        "search_notes_min_score": settings.search_notes_min_score,
        "project_context_min_score": settings.project_context_min_score,
        "resume_context_min_score": settings.resume_context_min_score,
        "agent_top_k": settings.agent_top_k,
    }
