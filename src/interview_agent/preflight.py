"""在启动服务前执行只读、本地且可理解的环境检查。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import socket
import sys

from pydantic import ValidationError

from interview_agent.application.runtime import (
    _load_index_documents,
    validate_local_storage_boundaries,
)
from interview_agent.core.config import Settings


_KNOWN_TRUNCATING_OUTPUT_TOKEN_LIMIT = 256
_RECOMMENDED_OUTPUT_TOKEN_LIMIT = 1_200


class CheckStatus(StrEnum):
    """启动检查的三个稳定结果级别。"""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    """一项面向人的检查结果和修复动作。"""

    code: str
    status: CheckStatus
    message: str
    action: str | None = None


def run_preflight(
    settings: Settings,
    *,
    check_port: bool = True,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> tuple[PreflightCheck, ...]:
    """验证 Python、配置、只读数据源、缓存条件和本地端口。"""
    if not isinstance(settings, Settings):
        raise ValueError("settings must be a Settings instance")
    checks: list[PreflightCheck] = []

    if sys.version_info >= (3, 11):
        checks.append(
            PreflightCheck(
                code="python_version",
                status=CheckStatus.PASS,
                message=f"Python {sys.version_info.major}.{sys.version_info.minor} 满足要求。",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                code="python_version",
                status=CheckStatus.FAIL,
                message="当前 Python 版本低于 3.11。",
                action="请使用项目 .venv 中的 Python 3.11 或更高版本。",
            )
        )

    if settings.llm_api_key is None:
        checks.append(
            PreflightCheck(
                code="llm_api_key",
                status=CheckStatus.FAIL,
                message="未配置 LLM_API_KEY，聊天问答无法运行。",
                action="请在未提交的 .env 中设置本次允许使用的模型密钥。",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                code="llm_api_key",
                status=CheckStatus.PASS,
                message="LLM_API_KEY 已配置；检查过程不会使用或显示它。",
            )
        )

    checks.append(_llm_output_budget_check(settings.llm_max_tokens))
    checks.append(
        _runtime_profile_check(
            settings.database_path,
            settings.vector_store_path,
        )
    )

    source_paths: tuple[Path, Path, Path] | None = None
    try:
        source_paths = validate_local_storage_boundaries(settings)
    except Exception:
        checks.append(
            PreflightCheck(
                code="storage_boundaries",
                status=CheckStatus.FAIL,
                message="数据源不存在、相互重叠，或运行时路径进入了只读数据源。",
                action=(
                    "请检查三个 SOURCE_DIRECTORY、ALLOWED_DATA_DIRECTORIES、"
                    "DATABASE_PATH、VECTOR_STORE_PATH 和 EMBEDDING_CACHE_DIRECTORY。"
                ),
            )
        )
    else:
        checks.append(
            PreflightCheck(
                code="storage_boundaries",
                status=CheckStatus.PASS,
                message="三个只读数据源互斥，运行时写入路径已隔离。",
            )
        )

    if source_paths is not None:
        try:
            documents = _load_index_documents(
                source_paths,
                allowed_directories=settings.allowed_data_directories,
                max_file_size_bytes=settings.markdown_max_file_size_bytes,
                max_total_size_bytes=settings.markdown_max_total_size_bytes,
                max_chunk_characters=settings.markdown_chunk_max_characters,
            )
        except Exception:
            checks.append(
                PreflightCheck(
                    code="markdown_sources",
                    status=CheckStatus.FAIL,
                    message="Markdown 数据源无法按当前白名单、编码或大小上限安全读取。",
                    action="请检查目录白名单、UTF-8 编码、文件大小和读取权限。",
                )
            )
        else:
            status = CheckStatus.PASS if documents else CheckStatus.WARNING
            message = (
                f"只读扫描完成，共发现 {len(documents)} 个可索引 Markdown 文档。"
                if documents
                else "三个数据源均为空，应用可启动但无法检索证据。"
            )
            checks.append(
                PreflightCheck(
                    code="markdown_sources",
                    status=status,
                    message=message,
                    action=(
                        None
                        if documents
                        else "请向至少一个已配置数据源加入 UTF-8 Markdown。"
                    ),
                )
            )

    cache_ready = _directory_has_content(settings.embedding_cache_directory)
    if settings.embedding_local_files_only and not cache_ready:
        checks.append(
            PreflightCheck(
                code="embedding_cache",
                status=CheckStatus.FAIL,
                message="已启用纯离线 Embedding，但模型缓存目录为空或不存在。",
                action=(
                    "请先准备完整模型缓存，或明确将 "
                    "EMBEDDING_LOCAL_FILES_ONLY 设为 false 允许首次下载公开模型。"
                ),
            )
        )
    elif cache_ready:
        checks.append(
            PreflightCheck(
                code="embedding_cache",
                status=CheckStatus.PASS,
                message="本地 Embedding 缓存目录已准备。",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                code="embedding_cache",
                status=CheckStatus.WARNING,
                message="未发现本地 Embedding 缓存，首次提问可能下载公开模型。",
                action="下载只涉及公开模型文件，不会上传 Vault 正文。",
            )
        )

    if check_port:
        if _port_is_available(host, port):
            checks.append(
                PreflightCheck(
                    code="local_port",
                    status=CheckStatus.PASS,
                    message=f"本地地址 http://{host}:{port} 可用。",
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    code="local_port",
                    status=CheckStatus.FAIL,
                    message=f"本地端口 {port} 已被其他进程占用。",
                    action="请先运行 stop.cmd，或关闭占用 8000 端口的进程。",
                )
            )
    return tuple(checks)


def _llm_output_budget_check(max_tokens: int) -> PreflightCheck:
    """拒绝已经用真实问题证明会截断的验收输出预算。"""
    if max_tokens <= _KNOWN_TRUNCATING_OUTPUT_TOKEN_LIMIT:
        return PreflightCheck(
            code="llm_output_budget",
            status=CheckStatus.FAIL,
            message=(
                f"LLM_MAX_TOKENS={max_tokens} 属于验收级小预算，"
                "日常回答可能在完成前被截断。"
            ),
            action=(
                "请在未提交的 .env 中设置 LLM_MAX_TOKENS=1200；"
                "这只是上限，不会强制消耗全部 token。"
            ),
        )
    if max_tokens < _RECOMMENDED_OUTPUT_TOKEN_LIMIT:
        return PreflightCheck(
            code="llm_output_budget",
            status=CheckStatus.WARNING,
            message=(
                f"LLM_MAX_TOKENS={max_tokens} 低于当前产品推荐值 1200，"
                "较长回答仍可能截断。"
            ),
            action="如出现 finish_reason=length，请改为 1200 后重启。",
        )
    return PreflightCheck(
        code="llm_output_budget",
        status=CheckStatus.PASS,
        message=f"LLM 输出上限为 {max_tokens} token，满足当前产品基线。",
    )


def _runtime_profile_check(
    database_path: Path,
    vector_store_path: Path,
) -> PreflightCheck:
    """识别验收命名的运行时路径，避免测试状态被误作日常历史。"""
    paths = (database_path, vector_store_path)
    if any(_looks_like_acceptance_path(path) for path in paths):
        return PreflightCheck(
            code="runtime_profile",
            status=CheckStatus.WARNING,
            message="SQLite 或向量目录仍使用验收命名，日常历史会与验收状态混在一起。",
            action=(
                "建议分别设置 DATABASE_PATH=data/interview_agent.db 和 "
                "VECTOR_STORE_PATH=vector_index；应用不会自动移动或删除旧数据。"
            ),
        )
    return PreflightCheck(
        code="runtime_profile",
        status=CheckStatus.PASS,
        message="运行时路径使用日常产品配置，不带验收目录标记。",
    )


def _looks_like_acceptance_path(path: Path) -> bool:
    """只检查路径分段名称，不解析内容或创建目录。"""
    return any(
        part.casefold() == "acceptance"
        or part.casefold().startswith("acceptance-")
        for part in path.parts
    )


def _directory_has_content(path: Path) -> bool:
    """只检查缓存根是否非空，不解析或加载模型。"""
    try:
        resolved = path.expanduser().resolve(strict=True)
        return resolved.is_dir() and next(resolved.iterdir(), None) is not None
    except (OSError, RuntimeError):
        return False


def _port_is_available(host: str, port: int) -> bool:
    """尝试绑定固定 loopback 端口，立即释放且不扫描其他端口。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((host, port))
    except OSError:
        return False
    return True


def _configuration_error_checks(
    error: ValidationError,
) -> tuple[PreflightCheck, ...]:
    """只展示字段与规则，不包含可能敏感的配置输入值。"""
    checks: list[PreflightCheck] = []
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        field = ".".join(str(part) for part in item.get("loc", ())) or "配置"
        checks.append(
            PreflightCheck(
                code="invalid_configuration",
                status=CheckStatus.FAIL,
                message=f"{field} 配置无效：{item.get('msg', '格式错误')}。",
                action="请修改未提交的 .env 后重新运行启动检查。",
            )
        )
    return tuple(checks)


def _print_report(checks: tuple[PreflightCheck, ...]) -> None:
    """以适合 PowerShell 和双击窗口阅读的中文格式输出。"""
    labels = {
        CheckStatus.PASS: "通过",
        CheckStatus.WARNING: "提醒",
        CheckStatus.FAIL: "失败",
    }
    print("Interview Agent 启动前检查")
    print("=" * 36)
    for check in checks:
        print(f"[{labels[check.status]}] {check.message}")
        if check.action:
            print(f"       处理：{check.action}")
    failures = sum(check.status is CheckStatus.FAIL for check in checks)
    warnings = sum(check.status is CheckStatus.WARNING for check in checks)
    print("=" * 36)
    if failures:
        print(f"检查未通过：{failures} 项失败，{warnings} 项提醒。")
    else:
        print(f"检查通过：0 项失败，{warnings} 项提醒。")


def main(argv: list[str] | None = None) -> int:
    """命令行入口；默认固定检查 127.0.0.1:8000。"""
    parser = argparse.ArgumentParser(description="Interview Agent 启动前检查")
    parser.add_argument(
        "--skip-port",
        action="store_true",
        help="只在已确认端口状态的受控测试中跳过端口检查",
    )
    arguments = parser.parse_args(argv)
    try:
        settings = Settings()
    except ValidationError as error:
        checks = _configuration_error_checks(error)
    else:
        checks = run_preflight(settings, check_port=not arguments.skip_port)
    _print_report(checks)
    return 1 if any(check.status is CheckStatus.FAIL for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CheckStatus",
    "PreflightCheck",
    "main",
    "run_preflight",
]
