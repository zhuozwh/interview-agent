"""验证 Windows 双击入口在系统 PowerShell 下保持可解析、可归因。"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UTF8_BOM = b"\xef\xbb\xbf"


def test_powershell_entrypoints_have_utf8_bom() -> None:
    """Windows PowerShell 5.1 需要 BOM 才会按 UTF-8 解码中文脚本。"""
    for relative_path in ("scripts/start.ps1", "scripts/stop.ps1"):
        script = PROJECT_ROOT / relative_path
        assert script.read_bytes().startswith(UTF8_BOM), relative_path


def test_cmd_entrypoints_preserve_powershell_exit_code() -> None:
    """双击入口不得用 pause 的成功状态掩盖 PowerShell 启动失败。"""
    for relative_path in ("start.cmd", "stop.cmd"):
        wrapper = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert 'set "interviewAgentExitCode=%errorlevel%"' in wrapper
        assert "exit /b %interviewAgentExitCode%" in wrapper


def test_start_script_normalizes_duplicate_windows_path_keys() -> None:
    """宿主同时注入 Path/PATH 时，PowerShell 5.1 仍可创建服务进程。"""
    script = (PROJECT_ROOT / "scripts/start.ps1").read_text(encoding="utf-8-sig")
    assert "function Repair-DuplicateProcessPathKeys" in script
    assert "Repair-DuplicateProcessPathKeys\n" in script
