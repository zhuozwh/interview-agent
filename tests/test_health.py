import asyncio

import httpx

from interview_agent.core.config import Settings
from interview_agent.main import create_app


def test_health_returns_ok() -> None:
    settings = Settings(_env_file=None)
    application = create_app(settings)

    async def request_health() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get("/health")

    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
