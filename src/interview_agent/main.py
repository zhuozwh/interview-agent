"""组装 FastAPI 应用，并提供本地启动入口。"""

from contextlib import asynccontextmanager

# Uvicorn 负责监听端口；FastAPI 负责路由和请求处理。
import uvicorn
from fastapi import FastAPI

# 使用别名是为了以后存在多个 router 时仍能看出各自来源。
from interview_agent.api.ask import router as ask_router
from interview_agent.api.health import router as health_router
from interview_agent.application import AskService, LazyLocalAskService
from interview_agent.core.config import Settings, get_settings
from interview_agent.core.logging import configure_logging


def create_app(
    settings: Settings | None = None,
    *,
    ask_service: AskService | None = None,
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

    # 把局部路由表装入应用；HTTP 层不直接持有 Agent 或存储实现。
    application.include_router(health_router)
    application.include_router(ask_router)
    return application


# 模块被加载时创建默认应用，Uvicorn 和其他 ASGI 工具约定通常把它命名为 app。
app = create_app()


def main() -> None:
    """在本机 8000 端口运行开发服务。"""
    # 127.0.0.1 只允许本机访问；log_config=None 避免 Uvicorn 覆盖项目日志配置。
    uvicorn.run(app, host="127.0.0.1", port=8000, log_config=None)


# 只有 python -m interview_agent.main 直接运行本模块时才启动服务；被测试导入时不会启动。
if __name__ == "__main__":
    main()
