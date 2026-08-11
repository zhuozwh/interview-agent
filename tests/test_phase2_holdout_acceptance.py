"""验证 v0.4.1 校准/留出协议不会用留出集反向选择阈值。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from interview_agent.acceptance import (
    VaultAcceptanceError,
    load_acceptance_cases,
    run_real_vault_acceptance,
)
from interview_agent.core.config import Settings

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "phase2_v041"


class HoldoutEmbedding:
    """为匿名语料制造可复现的命中和事实主题碰撞。"""

    model_name = "phase2-holdout-embedding-v1"
    dimension = 5

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self._vector(query)

    @staticmethod
    def _vector(text: str) -> list[float]:
        if "智能指针" in text or "RAII" in text:
            return [1.0, 0.0, 0.0, 0.0, 0.0]
        if "Reactor" in text or "Kafka" in text or "事件循环" in text:
            return [0.0, 1.0, 0.0, 0.0, 0.0]
        if "实习" in text or "后端经历" in text:
            return [0.0, 0.0, 1.0, 0.0, 0.0]
        if "邮箱服务" in text:
            return [0.0, 0.0, 0.0, 1.0, 0.0]
        return [0.0, 0.0, 0.0, 0.0, 1.0]


def test_split_protocol_keeps_holdout_out_of_threshold_selection(
    tmp_path: Path,
) -> None:
    """留出集独立过门槛，且报告中不存在“最佳留出阈值”。"""
    settings = _settings(tmp_path)
    source_files = tuple(_FIXTURE_ROOT.rglob("*.md"))
    before = {path: path.read_bytes() for path in source_files}

    report = run_real_vault_acceptance(
        settings,
        load_acceptance_cases(_FIXTURE_ROOT / "cases.json"),
        report_path=tmp_path / "report.json",
        embedding_provider=HoldoutEmbedding(),
    )

    assert report["schema_version"] == 3
    assert report["evaluation_protocol"] == "calibration_holdout"
    assert report["acceptance_passed"] is True
    assert report["phase2_triggers"] == []
    split_metrics = report["metrics"]["per_evaluation_split"]
    for split in ("calibration", "holdout"):
        assert split_metrics[split]["route_accuracy"] == 1.0
        assert split_metrics[split]["hit_at_1"] == 1.0
        assert split_metrics[split]["hard_negative_rejection_rate"] == 1.0
        assert split_metrics[split]["cross_namespace_rejection_rate"] == 1.0
        assert split_metrics[split]["agent_boundary_pass_rate"] == 1.0
    for namespace in ("notes", "projects", "resume"):
        assert (
            "threshold_only_best"
            not in report["metrics"]["per_namespace"][namespace]
        )
        assert (
            "threshold_only_best"
            in split_metrics["calibration"]["per_namespace"][namespace]
        )
        assert (
            "threshold_only_best"
            not in split_metrics["holdout"]["per_namespace"][namespace]
        )
        transfer = report["metrics"]["calibration_threshold_transfer"][
            namespace
        ]
        assert transfer["selected_from"] == "calibration"
        assert split_metrics["holdout"]["per_namespace"][namespace][
            "actual_policy_aware"
        ]["weighted_error_cost"] == 0
    assert report["metrics"]["calibration_threshold_transfer"]["notes"][
        "holdout"
    ]["weighted_error_cost"] == 0
    assert report["metrics"]["calibration_threshold_transfer"]["resume"][
        "holdout"
    ]["weighted_error_cost"] == 0
    # 项目留出集故意包含只命中 Reactor、缺少 Kafka 的合取事实；纯阈值
    # 会误接受，策略感知结果则拒绝，证明 holdout 没被调参抹平。
    assert report["metrics"]["calibration_threshold_transfer"]["projects"][
        "holdout"
    ]["weighted_error_cost"] == 4
    assert {path: path.read_bytes() for path in source_files} == before

    serialized = (tmp_path / "report.json").read_text(encoding="utf-8")
    case_payload = json.loads(
        (_FIXTURE_ROOT / "cases.json").read_text(encoding="utf-8")
    )
    assert all(case["question"] not in serialized for case in case_payload["cases"])
    for forbidden in (
        "fixture@example.com",
        "13812345678",
        "file:///X:/fixture/resume.docx",
        str(_FIXTURE_ROOT),
    ):
        assert forbidden not in serialized


def test_schema_v3_requires_both_evaluation_splits(tmp_path: Path) -> None:
    """只有 calibration 的文件不能伪装成留出验收。"""
    payload = json.loads(
        (_FIXTURE_ROOT / "cases.json").read_text(encoding="utf-8")
    )
    payload["cases"] = [
        case
        for case in payload["cases"]
        if case["evaluation_split"] == "calibration"
    ]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(VaultAcceptanceError, match="schema version"):
        load_acceptance_cases(path)


def test_holdout_failure_cannot_be_hidden_by_calibration_pass(
    tmp_path: Path,
) -> None:
    """总体平均值不能掩盖留出集关键正例失败。"""
    payload = json.loads(
        (_FIXTURE_ROOT / "cases.json").read_text(encoding="utf-8")
    )
    holdout_case = next(
        case for case in payload["cases"] if case["case_id"] == "HNP01"
    )
    holdout_case["probes"][0]["expected_paths"] = ["missing.md"]
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    report = run_real_vault_acceptance(
        _settings(tmp_path),
        load_acceptance_cases(cases_path),
        report_path=tmp_path / "report.json",
        embedding_provider=HoldoutEmbedding(),
    )

    assert report["acceptance_passed"] is False
    assert report["metrics"]["per_evaluation_split"]["calibration"][
        "hit_at_1"
    ] == 1.0
    assert "holdout_critical_positive_failed" in report["phase2_triggers"]
    assert not any(
        trigger.startswith("calibration_")
        for trigger in report["phase2_triggers"]
    )


def test_split_protocol_rejects_incomplete_namespace_coverage(
    tmp_path: Path,
) -> None:
    """任一 split 缺少 namespace 负例时必须在创建运行时前停止。"""
    payload = json.loads(
        (_FIXTURE_ROOT / "cases.json").read_text(encoding="utf-8")
    )
    payload["cases"] = [
        case for case in payload["cases"] if case["case_id"] != "CRN01"
    ]
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    settings = _settings(tmp_path)

    with pytest.raises(VaultAcceptanceError, match="Each evaluation split"):
        run_real_vault_acceptance(
            settings,
            load_acceptance_cases(cases_path),
            report_path=tmp_path / "report.json",
            embedding_provider=HoldoutEmbedding(),
        )
    assert not settings.database_path.exists()
    assert not settings.vector_store_path.exists()


def _settings(root: Path) -> Settings:
    """把仓库内只读匿名源与临时运行时明确隔离。"""
    notes = _FIXTURE_ROOT / "notes"
    projects = _FIXTURE_ROOT / "projects"
    resume = _FIXTURE_ROOT / "resume"
    return Settings(
        _env_file=None,
        markdown_source_directory=notes,
        project_source_directory=projects,
        resume_source_directory=resume,
        allowed_data_directories=(notes, projects, resume),
        database_path=root / "runtime" / "state.sqlite3",
        vector_store_path=root / "runtime" / "vector",
        vector_collection_name="phase2_holdout_acceptance",
        embedding_cache_directory=root / "runtime" / "cache",
        embedding_local_files_only=True,
        search_notes_min_score=0.5,
        project_context_min_score=0.5,
        resume_context_min_score=0.5,
        agent_top_k=3,
    )
