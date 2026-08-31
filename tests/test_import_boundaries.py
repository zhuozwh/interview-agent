"""验证公开包在独立进程中的导入顺序不会形成循环依赖。"""

import subprocess
import sys


def test_application_and_storage_support_both_import_orders() -> None:
    """用户、脚本和测试不应依赖某个偶然的模块预热顺序。"""
    commands = (
        "import interview_agent.storage; import interview_agent.application",
        "import interview_agent.application; import interview_agent.storage",
    )

    for command in commands:
        result = subprocess.run(
            [sys.executable, "-c", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
