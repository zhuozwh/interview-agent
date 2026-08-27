"""提供仅供本机启动脚本使用的受控停止入口。"""

from __future__ import annotations

from hmac import compare_digest
import os

from fastapi import APIRouter, BackgroundTasks, Header, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/system")
_TOKEN_ENVIRONMENT_VARIABLE = "INTERVIEW_AGENT_SHUTDOWN_TOKEN"


@router.post("/shutdown", include_in_schema=False)
def shutdown_local_service(
    request: Request,
    background_tasks: BackgroundTasks,
    shutdown_token: str | None = Header(
        default=None,
        alias="X-Interview-Agent-Shutdown-Token",
    ),
) -> JSONResponse:
    """令牌匹配后在响应发送完成时请求 Uvicorn 优雅退出。"""
    expected = os.environ.get(_TOKEN_ENVIRONMENT_VARIABLE)
    callback = getattr(request.app.state, "shutdown_callback", None)
    if (
        not expected
        or not shutdown_token
        or len(expected) > 256
        or len(shutdown_token) > 256
        or not compare_digest(expected, shutdown_token)
        or not callable(callback)
    ):
        # 对未授权调用统一伪装成不存在，避免暴露控制入口状态。
        return JSONResponse(
            status_code=404,
            content={"detail": "Not found."},
        )
    background_tasks.add_task(callback)
    return JSONResponse(content={"status": "stopping"})


__all__ = ["_TOKEN_ENVIRONMENT_VARIABLE", "router"]
