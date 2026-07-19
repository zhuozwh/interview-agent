"""验证 FastAPI 健康检查接口的状态码和 JSON 内容。"""

import asyncio

import httpx

from interview_agent.core.config import Settings
from interview_agent.main import create_app


def test_health_returns_ok() -> None:
    # 不读取用户本机 .env，避免本地配置改变测试结果。
    settings = Settings(_env_file=None)
    application = create_app(settings)

    async def request_health() -> httpx.Response:
        # ASGITransport 直接在内存中调用 FastAPI，不启动服务器，也不访问真实网络。
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            # 相对路径 /health 需要基础地址；testserver 只是占位，不会做 DNS 请求。
            base_url="http://testserver",
        ) as client:
            return await client.get("/health")

    # 从普通同步测试中运行上面的异步请求函数，并取得 HTTP 响应对象。
    response = asyncio.run(request_health())

    # 两条断言都成立时，pytest 才会把这个测试标记为通过。
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
