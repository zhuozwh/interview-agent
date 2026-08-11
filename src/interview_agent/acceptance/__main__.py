"""提供显式启用的真实 Vault 只读验收命令。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from interview_agent.acceptance import (
    VaultAcceptanceError,
    load_acceptance_cases,
    run_real_vault_acceptance,
)
from interview_agent.core.config import Settings


def main(argv: list[str] | None = None) -> int:
    """解析本地路径并返回可供自动化判断的退出码。"""
    parser = argparse.ArgumentParser(
        description="Run opt-in read-only acceptance against local Vault sources.",
    )
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--allow-incomplete-sources",
        action="store_true",
        help="Run a pre-baseline while clearly marking missing namespaces.",
    )
    arguments = parser.parse_args(argv)
    if os.getenv("RUN_REAL_VAULT_ACCEPTANCE") != "1":
        parser.error("set RUN_REAL_VAULT_ACCEPTANCE=1 to read real Vault sources")
    try:
        report = run_real_vault_acceptance(
            Settings(),
            load_acceptance_cases(arguments.cases),
            report_path=arguments.report,
            allow_incomplete_sources=arguments.allow_incomplete_sources,
        )
    except VaultAcceptanceError as error:
        print(str(error), file=sys.stderr)
        return 1
    summary = {
        "acceptance_passed": report["acceptance_passed"],
        "evaluation_protocol": report["evaluation_protocol"],
        "formal_complete": report["formal_complete"],
        "safety_passed": report["safety_passed"],
        "quality_gates_passed": report["quality_gates_passed"],
        "missing_namespaces": report["missing_namespaces"],
        "metrics": report["metrics"],
        "phase2_triggers": report["phase2_triggers"],
        "failure_count": len(report["failures"]),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["acceptance_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
