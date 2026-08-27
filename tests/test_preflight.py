"""验证启动前检查只读、可归因且不访问远端服务。"""

from collections.abc import Iterator
from pathlib import Path
import socket
from tempfile import TemporaryDirectory

import pytest

from interview_agent.core.config import Settings
from interview_agent.preflight import CheckStatus, run_preflight


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """为数据源、缓存和运行时路径创建隔离目录。"""
    with TemporaryDirectory(prefix="interview-agent-preflight-") as directory:
        yield Path(directory)


def _settings(root: Path, **overrides) -> Settings:
    """创建三个互斥合成源和已准备的本地缓存。"""
    allowed = root / "knowledge"
    notes = allowed / "notes"
    projects = allowed / "projects"
    resume = allowed / "resume"
    for source in (notes, projects, resume):
        source.mkdir(parents=True, exist_ok=True)
    (notes / "raii.md").write_text("# RAII\n析构时释放资源。", encoding="utf-8")
    (projects / "server.md").write_text("# Reactor\n单事件循环。", encoding="utf-8")
    (resume / "resume.md").write_text("# 经历\n合成后端经历。", encoding="utf-8")
    cache = root / "models"
    cache.mkdir(exist_ok=True)
    (cache / "ready.marker").write_text("synthetic", encoding="utf-8")
    values = {
        "markdown_source_directory": notes,
        "project_source_directory": projects,
        "resume_source_directory": resume,
        "allowed_data_directories": (allowed,),
        "database_path": root / "runtime" / "agent.sqlite3",
        "vector_store_path": root / "vectors",
        "embedding_cache_directory": cache,
        "embedding_local_files_only": True,
        "llm_api_key": "test-only-key",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def _by_code(checks):
    """把稳定代码映射到检查结果，便于验证失败归因。"""
    return {check.code: check for check in checks}


def test_preflight_passes_valid_synthetic_local_environment(
    temporary_directory: Path,
) -> None:
    """合成数据、缓存和隔离路径齐备时没有失败项或运行时写入。"""
    settings = _settings(temporary_directory)
    checks = run_preflight(settings, check_port=False)
    results = _by_code(checks)

    assert all(check.status is not CheckStatus.FAIL for check in checks)
    assert results["storage_boundaries"].status is CheckStatus.PASS
    assert results["markdown_sources"].status is CheckStatus.PASS
    assert "3 个" in results["markdown_sources"].message
    assert results["embedding_cache"].status is CheckStatus.PASS
    assert not settings.database_path.exists()
    assert not settings.vector_store_path.exists()


def test_preflight_reports_missing_key_without_exposing_values(
    temporary_directory: Path,
) -> None:
    """缺少密钥是独立失败，报告不含任何密钥输入。"""
    settings = _settings(temporary_directory, llm_api_key=None)
    checks = run_preflight(settings, check_port=False)
    key_check = _by_code(checks)["llm_api_key"]

    assert key_check.status is CheckStatus.FAIL
    assert "LLM_API_KEY" in key_check.message
    assert "test-only-key" not in repr(checks)


def test_preflight_rejects_known_truncating_llm_output_budget(
    temporary_directory: Path,
) -> None:
    """已在真实口述问题中触发截断的 256 token 配置不得继续绿灯。"""
    settings = _settings(temporary_directory, llm_max_tokens=256)
    check = _by_code(run_preflight(settings, check_port=False))[
        "llm_output_budget"
    ]

    assert check.status is CheckStatus.FAIL
    assert "256" in check.message
    assert check.action is not None
    assert "1200" in check.action


def test_preflight_classifies_output_budget_boundaries(
    temporary_directory: Path,
) -> None:
    """257–1199 只提醒，产品基线 1200 必须稳定通过。"""
    expected = (
        (257, CheckStatus.WARNING),
        (1_199, CheckStatus.WARNING),
        (1_200, CheckStatus.PASS),
    )
    for max_tokens, status in expected:
        settings = _settings(
            temporary_directory,
            llm_max_tokens=max_tokens,
        )
        check = _by_code(run_preflight(settings, check_port=False))[
            "llm_output_budget"
        ]
        assert check.status is status


def test_preflight_warns_when_product_uses_acceptance_runtime_paths(
    temporary_directory: Path,
) -> None:
    """验收 SQLite/Chroma 混入日常运行时应明确提醒，但不删除旧数据。"""
    acceptance_root = temporary_directory / "acceptance-v0.3.1-baseline"
    settings = _settings(
        temporary_directory,
        database_path=acceptance_root / "state.sqlite3",
        vector_store_path=acceptance_root / "vectors",
    )
    check = _by_code(run_preflight(settings, check_port=False))[
        "runtime_profile"
    ]

    assert check.status is CheckStatus.WARNING
    assert "验收" in check.message
    assert check.action is not None
    assert "不会自动移动或删除" in check.action


def test_preflight_rejects_runtime_path_inside_read_only_source(
    temporary_directory: Path,
) -> None:
    """preflight 在创建 SQLite 前复现 Phase 2 的运行时隔离边界。"""
    baseline = _settings(temporary_directory)
    unsafe_database = baseline.markdown_source_directory / "runtime.sqlite3"
    settings = Settings(
        **{
            **baseline.model_dump(),
            "database_path": unsafe_database,
            "llm_api_key": "test-only-key",
            "_env_file": None,
        }
    )
    checks = run_preflight(settings, check_port=False)

    assert _by_code(checks)["storage_boundaries"].status is CheckStatus.FAIL
    assert not unsafe_database.exists()


def test_preflight_distinguishes_required_and_optional_embedding_cache(
    temporary_directory: Path,
) -> None:
    """纯离线缺缓存失败，允许首次下载时只给出本地提醒。"""
    missing_cache = temporary_directory / "missing-models"
    offline = _settings(
        temporary_directory,
        embedding_cache_directory=missing_cache,
        embedding_local_files_only=True,
    )
    online_allowed = Settings(
        **{
            **offline.model_dump(),
            "embedding_local_files_only": False,
            "llm_api_key": "test-only-key",
            "_env_file": None,
        }
    )

    offline_check = _by_code(run_preflight(offline, check_port=False))[
        "embedding_cache"
    ]
    online_check = _by_code(run_preflight(online_allowed, check_port=False))[
        "embedding_cache"
    ]
    assert offline_check.status is CheckStatus.FAIL
    assert online_check.status is CheckStatus.WARNING


def test_preflight_reports_exact_local_port_collision(
    temporary_directory: Path,
) -> None:
    """只检查传入端口，不扫描或自动改用其他端口。"""
    settings = _settings(temporary_directory)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        checks = run_preflight(
            settings,
            check_port=True,
            host="127.0.0.1",
            port=port,
        )
    port_check = _by_code(checks)["local_port"]
    assert port_check.status is CheckStatus.FAIL
    assert str(port) in port_check.message
