"""验证外部证据 HTTP 预览、确认、错误净化和零持久化。"""

import asyncio
from pathlib import Path

import httpx

from interview_agent.application import (
    ControlledExternalSearchService,
    ExternalSearchCandidate,
)
from interview_agent.core.config import Settings
from interview_agent.main import create_app


class SyntheticProvider:
    """记录唯一调用并返回合成官方资料。"""

    name = "Synthetic Search"

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, max_results: int):
        self.calls.append((query, max_results))
        if self.error is not None:
            raise self.error
        return (
            ExternalSearchCandidate(
                title="RAII official reference",
                url="https://docs.example.com/raii",
                snippet="<b>RAII</b> synthetic public reference.",
                source_type="official_documentation",
            ),
        )


async def _request(application, path: str, json: dict) -> httpx.Response:
    """通过内存 ASGI 调用接口，不监听端口。"""
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.post(path, json=json)


def test_default_preview_is_local_and_does_not_create_database(
    tmp_path: Path,
) -> None:
    """默认未配置提供方，预览仍可用且不触碰 SQLite。"""
    database_path = tmp_path / "runtime" / "history.sqlite3"
    application = create_app(
        Settings(database_path=database_path, _env_file=None)
    )

    preview = asyncio.run(
        _request(
            application,
            "/api/external-search/preview",
            {"question": "请联网搜索：RAII 官方资料"},
        )
    )
    search = asyncio.run(
        _request(
            application,
            "/api/external-search",
            {
                "question": "RAII 官方资料",
                "confirmed_query": "RAII 官方资料",
            },
        )
    )

    assert preview.status_code == 200
    assert preview.json()["allowed"] is True
    assert preview.json()["query"] == "RAII 官方资料"
    assert preview.json()["provider_configured"] is False
    assert search.status_code == 503
    assert "尚未配置" in search.json()["detail"]
    assert preview.headers["cache-control"] == "no-store"
    assert search.headers["cache-control"] == "no-store"
    assert not database_path.exists()


def test_confirmed_search_returns_separate_ephemeral_web_citations(
    tmp_path: Path,
) -> None:
    """合成提供方只被调用一次，结果明确为不持久化的 [W] 来源。"""
    provider = SyntheticProvider()
    service = ControlledExternalSearchService(provider)
    database_path = tmp_path / "runtime" / "history.sqlite3"
    application = create_app(
        Settings(database_path=database_path, _env_file=None),
        external_search_service=service,
    )

    response = asyncio.run(
        _request(
            application,
            "/api/external-search",
            {"question": "RAII 是什么？", "confirmed_query": "RAII 是什么？"},
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] is False
    assert body["provider_name"] == "Synthetic Search"
    assert body["sources"][0]["citation_id"] == "W1"
    assert body["sources"][0]["source_type"] == "official_documentation"
    assert body["sources"][0]["snippet"].startswith("<b>")
    assert provider.calls == [("RAII 是什么？", 5)]
    assert response.headers["pragma"] == "no-cache"
    assert not database_path.exists()


def test_policy_and_confirmation_fail_before_provider_call() -> None:
    """个人资料和被篡改确认都不能进入合成提供方。"""
    provider = SyntheticProvider()
    application = create_app(
        Settings(_env_file=None),
        external_search_service=ControlledExternalSearchService(provider),
    )

    refused = asyncio.run(
        _request(
            application,
            "/api/external-search",
            {
                "question": "我的项目中 Reactor 如何实现？",
                "confirmed_query": "Reactor 如何实现？",
            },
        )
    )
    mismatched = asyncio.run(
        _request(
            application,
            "/api/external-search",
            {"question": "RAII", "confirmed_query": "智能指针"},
        )
    )

    assert refused.status_code == 403
    assert "只能使用本地证据" in refused.json()["detail"]
    assert mismatched.status_code == 409
    assert "重新预览" in mismatched.json()["detail"]
    assert provider.calls == []


def test_provider_error_and_invalid_payload_do_not_leak_details() -> None:
    """提供方密钥片段和额外协议字段都不能进入成功路径。"""
    provider = SyntheticProvider(error=RuntimeError("secret-provider-key"))
    application = create_app(
        Settings(_env_file=None),
        external_search_service=ControlledExternalSearchService(provider),
    )

    failed = asyncio.run(
        _request(
            application,
            "/api/external-search",
            {"question": "RAII", "confirmed_query": "RAII"},
        )
    )
    invalid = asyncio.run(
        _request(
            application,
            "/api/external-search/preview",
            {"question": "RAII", "provider": "attacker"},
        )
    )

    assert failed.status_code == 503
    assert "secret-provider-key" not in failed.text
    assert invalid.status_code == 422
    assert provider.calls == [("RAII", 5)]
