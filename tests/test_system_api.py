"""验证一键停止入口只接受启动脚本持有的本机令牌。"""

import asyncio

import httpx

from interview_agent.api.system import _TOKEN_ENVIRONMENT_VARIABLE
from interview_agent.core.config import Settings
from interview_agent.main import create_app


async def _post(application, token: str | None = None) -> httpx.Response:
    """通过内存 ASGI transport 请求停止，不影响真实进程。"""
    headers = {}
    if token is not None:
        headers["X-Interview-Agent-Shutdown-Token"] = token
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.post("/api/system/shutdown", headers=headers)


def test_shutdown_requires_exact_token_and_registered_callback(monkeypatch) -> None:
    """缺失、错误令牌和没有回调都统一表现为不存在。"""
    monkeypatch.setenv(_TOKEN_ENVIRONMENT_VARIABLE, "expected-token")
    application = create_app(Settings(_env_file=None))

    assert asyncio.run(_post(application)).status_code == 404
    assert asyncio.run(_post(application, "wrong-token")).status_code == 404
    assert asyncio.run(_post(application, "expected-token")).status_code == 404


def test_shutdown_runs_callback_after_authorized_response(monkeypatch) -> None:
    """正确令牌只触发已登记回调，不执行任意命令。"""
    monkeypatch.setenv(_TOKEN_ENVIRONMENT_VARIABLE, "expected-token")
    application = create_app(Settings(_env_file=None))
    calls = []
    application.state.shutdown_callback = lambda: calls.append("stop")

    response = asyncio.run(_post(application, "expected-token"))
    assert response.status_code == 200
    assert response.json() == {"status": "stopping"}
    assert calls == ["stop"]


def test_shutdown_token_is_not_returned_or_documented(monkeypatch) -> None:
    """控制令牌不会进入响应正文或 OpenAPI 文档。"""
    token = "private-local-shutdown-token"
    monkeypatch.setenv(_TOKEN_ENVIRONMENT_VARIABLE, token)
    application = create_app(Settings(_env_file=None))
    application.state.shutdown_callback = lambda: None

    response = asyncio.run(_post(application, token))
    schema = application.openapi()
    assert token not in response.text
    assert "/api/system/shutdown" not in schema["paths"]
