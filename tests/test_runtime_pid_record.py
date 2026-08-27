"""验证一键启动使用真实服务 PID，而不是 Windows 启动器 PID。"""

import json
import os
from pathlib import Path
import sys

import pytest

from interview_agent.main import (
    _PID_FILE_ENVIRONMENT_VARIABLE,
    _SHUTDOWN_TOKEN_ENVIRONMENT_VARIABLE,
    _remove_own_runtime_record,
    _write_runtime_record,
)


def test_service_writes_and_removes_own_runtime_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """PID、解释器、启动时间和令牌由真实 Python 服务进程写入。"""
    run_directory = tmp_path / ".run"
    pid_file = run_directory / "interview-agent.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(_PID_FILE_ENVIRONMENT_VARIABLE, str(pid_file))
    monkeypatch.setenv(
        _SHUTDOWN_TOKEN_ENVIRONMENT_VARIABLE,
        "test-local-shutdown-token",
    )

    written = _write_runtime_record()
    assert written == pid_file.resolve()
    record = json.loads(pid_file.read_text(encoding="utf-8"))
    assert record["pid"] == os.getpid()
    assert Path(record["executable"]) == Path(
        getattr(sys, "_base_executable", sys.executable)
    ).resolve()
    assert Path(record["python_environment"]) == Path(sys.executable).resolve()
    assert record["shutdown_token"] == "test-local-shutdown-token"
    assert record["url"] == "http://127.0.0.1:8000/"
    assert record["started_at"].endswith("+00:00")

    _remove_own_runtime_record(written)
    assert not pid_file.exists()


def test_runtime_record_rejects_path_outside_project_run_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """环境变量不能诱导服务把令牌或 PID 写入任意位置。"""
    unsafe_file = tmp_path / "outside" / "interview-agent.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(_PID_FILE_ENVIRONMENT_VARIABLE, str(unsafe_file))
    monkeypatch.setenv(_SHUTDOWN_TOKEN_ENVIRONMENT_VARIABLE, "safe-token")

    with pytest.raises(RuntimeError, match="project .run"):
        _write_runtime_record()
    assert not unsafe_file.exists()


@pytest.mark.parametrize("token", ["", "contains space", "x" * 257])
def test_runtime_record_rejects_unsafe_shutdown_token(
    tmp_path: Path,
    monkeypatch,
    token: str,
) -> None:
    """停止令牌必须是有限可打印 ASCII，避免污染 JSON 和请求头。"""
    pid_file = tmp_path / ".run" / "interview-agent.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(_PID_FILE_ENVIRONMENT_VARIABLE, str(pid_file))
    monkeypatch.setenv(_SHUTDOWN_TOKEN_ENVIRONMENT_VARIABLE, token)

    with pytest.raises(RuntimeError, match="shutdown token"):
        _write_runtime_record()
    assert not pid_file.exists()
