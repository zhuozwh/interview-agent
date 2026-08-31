"""组装 FastAPI 应用，并提供本地启动入口。"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

# Uvicorn 负责监听端口；FastAPI 负责路由和请求处理。
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# 使用别名是为了以后存在多个 router 时仍能看出各自来源。
from interview_agent.api.ask import router as ask_router
from interview_agent.api.evidence import router as evidence_router
from interview_agent.api.external_search import router as external_search_router
from interview_agent.api.health import router as health_router
from interview_agent.api.history import router as history_router
from interview_agent.api.system import router as system_router
from interview_agent.api.web import _WEB_DIRECTORY, router as web_router
from interview_agent.application import (
    AskService,
    CitationEvidenceService,
    ControlledExternalSearchService,
    ConversationHistoryService,
    ExternalSearchService,
    LazyLocalAskService,
    LocalConversationHistoryService,
    LocalCitationEvidenceService,
)
from interview_agent.core.config import Settings, get_settings
from interview_agent.core.logging import configure_logging

_PID_FILE_ENVIRONMENT_VARIABLE = "INTERVIEW_AGENT_PID_FILE"
_SHUTDOWN_TOKEN_ENVIRONMENT_VARIABLE = "INTERVIEW_AGENT_SHUTDOWN_TOKEN"


def create_app(
    settings: Settings | None = None,
    *,
    ask_service: AskService | None = None,
    history_service: ConversationHistoryService | None = None,
    evidence_service: CitationEvidenceService | None = None,
    external_search_service: ExternalSearchService | None = None,
) -> FastAPI:
    """创建并组装一个 FastAPI 应用；测试可传入独立配置。"""
    # 调用者传了 settings 就直接使用；正常启动未传时再读取默认配置和环境变量。
    current_settings = settings or get_settings()

    # 先初始化日志，确保后续启动过程也使用统一的级别和格式。
    configure_logging(current_settings.log_level)

    current_ask_service = (
        ask_service
        if ask_service is not None
        else LazyLocalAskService(current_settings)
    )
    current_history_service = (
        history_service
        if history_service is not None
        else LocalConversationHistoryService(current_settings)
    )
    current_evidence_service = (
        evidence_service
        if evidence_service is not None
        else LocalCitationEvidenceService(
            current_settings,
            current_history_service,
        )
    )
    current_external_search_service = (
        external_search_service
        if external_search_service is not None
        else ControlledExternalSearchService()
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """退出应用时释放可能已经建立的 Chroma 和 HTTP 连接。"""
        yield
        close = getattr(application.state.ask_service, "close", None)
        if callable(close):
            close()

    # title 会显示在 FastAPI 自动生成的 /docs 页面中。
    application = FastAPI(
        title=current_settings.app_name,
        lifespan=lifespan,
    )

    # state 用于保存应用级共享对象；这里只保存配置，不是业务数据库状态。
    application.state.settings = current_settings
    application.state.ask_service = current_ask_service
    application.state.history_service = current_history_service
    application.state.evidence_service = current_evidence_service
    application.state.external_search_service = current_external_search_service
    application.state.shutdown_callback = None

    # 把局部路由表装入应用；HTTP 层不直接持有 Agent 或存储实现。
    application.mount(
        "/assets",
        StaticFiles(directory=_WEB_DIRECTORY),
        name="assets",
    )
    application.include_router(web_router)
    application.include_router(health_router)
    application.include_router(ask_router)
    application.include_router(history_router)
    application.include_router(evidence_router)
    application.include_router(external_search_router)
    application.include_router(system_router)
    return application


# 模块被加载时创建默认应用，Uvicorn 和其他 ASGI 工具约定通常把它命名为 app。
app = create_app()


def main() -> None:
    """在本机 8000 端口运行开发服务。"""
    # 127.0.0.1 只允许本机访问；log_config=None 避免 Uvicorn 覆盖项目日志配置。
    configuration = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=8000,
        log_config=None,
    )
    server = uvicorn.Server(configuration)
    app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    record_path = _write_runtime_record()
    try:
        server.run()
    finally:
        _remove_own_runtime_record(record_path)


def _write_runtime_record() -> Path | None:
    """由真实服务进程写入 PID，避开 Windows 启动器 PID 漂移。"""
    configured = os.environ.get(_PID_FILE_ENVIRONMENT_VARIABLE)
    if not configured:
        return None
    path = Path(configured).expanduser().resolve(strict=False)
    expected_directory = (Path.cwd() / ".run").resolve(strict=False)
    if path.parent != expected_directory or path.name != "interview-agent.json":
        raise RuntimeError("Runtime PID file must stay in the project .run directory.")
    token = os.environ.get(_SHUTDOWN_TOKEN_ENVIRONMENT_VARIABLE)
    if (
        not token
        or len(token) > 256
        or any(ord(character) < 33 or ord(character) > 126 for character in token)
    ):
        raise RuntimeError("A safe local shutdown token is required.")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Windows 虚拟环境的 sys.executable 可能是启动器路径，而进程映像实际使用
    # 基础解释器；PID 身份校验必须记录操作系统看到的真实映像路径。
    process_executable = Path(
        getattr(sys, "_base_executable", sys.executable)
    ).resolve(strict=True)
    record = {
        "pid": os.getpid(),
        "executable": str(process_executable),
        "python_environment": str(Path(sys.executable).resolve(strict=True)),
        "url": "http://127.0.0.1:8000/",
        "shutdown_token": token,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def _remove_own_runtime_record(path: Path | None) -> None:
    """仅当 PID 仍属于当前进程时清理记录，避免删除后来启动的实例。"""
    if path is None:
        return
    try:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        if record.get("pid") == os.getpid():
            path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return


# 只有 python -m interview_agent.main 直接运行本模块时才启动服务；被测试导入时不会启动。
if __name__ == "__main__":
    main()
