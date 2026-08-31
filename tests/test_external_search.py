"""验证外部查询预览、确认和临时来源的确定性安全边界。"""

from dataclasses import replace

import pytest

from interview_agent.application import (
    ControlledExternalSearchService,
    ExternalSearchCandidate,
    ExternalSearchConfirmationError,
    ExternalSearchPolicyRefusedError,
    ExternalSearchUnavailableError,
)


class SyntheticProvider:
    """只返回合成公开来源，不访问网络。"""

    name = "Synthetic Search"

    def __init__(
        self,
        results: tuple[ExternalSearchCandidate, ...] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self.results = results
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, max_results: int):
        self.calls.append((query, max_results))
        if self.error is not None:
            raise self.error
        return self.results


def _candidate(
    *,
    title: str = "C++ RAII reference",
    url: str = "https://docs.example.com/cpp/raii#overview",
    snippet: str = "RAII binds resource lifetime to object lifetime.",
    source_type: str = "official_documentation",
) -> ExternalSearchCandidate:
    """构造不包含真实网页内容的外部候选。"""
    return ExternalSearchCandidate(
        title=title,
        url=url,
        snippet=snippet,
        source_type=source_type,
    )


def test_preview_is_local_normalized_and_provider_aware() -> None:
    """预览移除搜索请求前缀，但默认服务不会因此联网。"""
    offline = ControlledExternalSearchService()
    configured = ControlledExternalSearchService(SyntheticProvider())

    offline_preview = offline.preview("  请联网搜索一下：RAII 的官方资料  ")
    configured_preview = configured.preview("RAII 的官方资料")

    assert offline_preview.allowed is True
    assert offline_preview.query == "RAII 的官方资料"
    assert offline_preview.provider_configured is False
    assert offline_preview.provider_name is None
    assert configured_preview.provider_configured is True
    assert configured_preview.provider_name == "Synthetic Search"


@pytest.mark.parametrize(
    "question",
    (
        "根据我的简历介绍后端经历",
        "我的项目中 Reactor 当前如何实现？",
        "复盘这场面试并评价我的表现",
        "请列出完整简历和全部联系方式",
        "我在上一家公司做的项目适合怎么介绍？",
        "请根据刚才的回答补充外部资料",
    ),
)
def test_preview_refuses_personal_context_and_bulk_exfiltration(
    question: str,
) -> None:
    """外部信息不能替代本地项目、简历或面试事实。"""
    preview = ControlledExternalSearchService().preview(question)

    assert preview.allowed is False
    assert preview.reason_code in {
        "personal_context_requires_local_evidence",
        "sensitive_bulk_exfiltration_refused",
    }


def test_preview_redacts_and_blocks_personal_identifiers() -> None:
    """即使能生成安全预览，发生脱敏也必须停止而不是静默外发。"""
    question = (
        "搜索 candidate@example.com 在 "
        r"C:\Users\candidate\resume.md 中的资料"
    )

    preview = ControlledExternalSearchService().preview(question)

    assert preview.allowed is False
    assert preview.reason_code == "sensitive_data_detected"
    assert preview.query is not None
    assert "candidate@example.com" not in preview.query
    assert r"C:\Users\candidate" not in preview.query
    assert "[REDACTED_EMAIL]" in preview.query
    assert "[REDACTED_LOCAL_PATH]" in preview.query


@pytest.mark.parametrize(
    "question",
    (
        "搜索 api_key=synthetic-secret-value 的用法",
        "搜索 access_token:abc123456789 的问题",
        "查询 sk-synthetic1234567890 对应文档",
        "查询 password=synthetic-password 的报错",
        "查询 Bearer abcdefghijklmnop 的用法",
        "查询 ghp_abcdefghijklmnopqrstuvwxyz123456 的权限",
    ),
)
def test_preview_redacts_and_blocks_secret_values(question: str) -> None:
    """常见密钥形态不能被当成普通搜索词发送给提供方。"""
    preview = ControlledExternalSearchService().preview(question)

    assert preview.allowed is False
    assert preview.reason_code == "sensitive_data_detected"
    assert preview.query is not None
    assert "synthetic-secret-value" not in preview.query
    assert "abc123456789" not in preview.query
    assert "synthetic1234567890" not in preview.query
    assert "[REDACTED_SECRET]" in preview.query


def test_search_recomputes_policy_and_rejects_tampered_confirmation() -> None:
    """浏览器不能用一次安全预览确认另一条查询。"""
    provider = SyntheticProvider((_candidate(),))
    service = ControlledExternalSearchService(provider)

    with pytest.raises(ExternalSearchConfirmationError):
        service.search("RAII 是什么？", "另一条查询")
    with pytest.raises(ExternalSearchPolicyRefusedError):
        service.search("我的项目实现了什么？", "我的项目实现了什么？")

    assert provider.calls == []


def test_search_normalizes_sources_and_calls_provider_once() -> None:
    """外部来源独立编号，危险 URL 被过滤，HTML 仍只是文本。"""
    first = _candidate(
        snippet="  <script>alert('external')</script>   RAII reference  ",
    )
    duplicate = replace(first, title="duplicate", url=first.url.replace("#overview", "#other"))
    private = replace(first, url="https://127.0.0.1/private")
    insecure = replace(first, url="http://docs.example.com/insecure")
    credential = replace(first, url="https://user:pass@docs.example.com/private")
    secret_query = replace(first, url="https://docs.example.com/raii?token=secret")
    password_query = replace(first, url="https://docs.example.com/raii?password=secret")
    custom_port = replace(first, url="https://docs.example.com:8443/raii")
    whitespace_url = replace(first, url="https://docs.example.com/a b")
    second = _candidate(
        title="RAII paper",
        url="https://papers.example.org/raii?lang=en",
        source_type="paper",
    )
    unknown_type = _candidate(
        title="RAII article",
        url="https://engineering.example.net/raii",
        source_type="untrusted_category",
    )
    provider = SyntheticProvider(
        (
            first,
            duplicate,
            private,
            insecure,
            credential,
            secret_query,
            password_query,
            custom_port,
            whitespace_url,
            second,
            unknown_type,
        )
    )
    service = ControlledExternalSearchService(provider)

    result = service.search("RAII 是什么？", "RAII 是什么？")

    assert provider.calls == [("RAII 是什么？", 5)]
    assert result.provider_name == "Synthetic Search"
    assert [source.citation_id for source in result.sources] == ["W1", "W2", "W3"]
    assert result.sources[0].url == "https://docs.example.com/cpp/raii"
    assert result.sources[0].snippet.startswith("<script>")
    assert result.sources[1].source_type == "paper"
    assert result.sources[2].source_type == "web"
    assert all("127.0.0.1" not in source.url for source in result.sources)
    assert all("token=" not in source.url for source in result.sources)
    assert all("user:pass" not in source.url for source in result.sources)
    assert all(source.url.startswith("https://") for source in result.sources)


def test_unconfigured_or_failed_provider_returns_stable_unavailable_error() -> None:
    """没有提供方或提供方异常都不能穿透底层消息。"""
    with pytest.raises(ExternalSearchUnavailableError):
        ControlledExternalSearchService().search("RAII", "RAII")

    provider = SyntheticProvider(error=RuntimeError("secret-provider-key"))
    with pytest.raises(
        ExternalSearchUnavailableError,
        match="could not complete",
    ):
        ControlledExternalSearchService(provider).search("RAII", "RAII")
    assert provider.calls == [("RAII", 5)]


def test_provider_collection_and_size_are_bounded() -> None:
    """提供方不能用错误集合类型或超量候选扩大内存边界。"""
    class ListProvider(SyntheticProvider):
        def search(self, query: str, *, max_results: int):
            self.calls.append((query, max_results))
            return [_candidate()]

    with pytest.raises(ExternalSearchUnavailableError, match="collection"):
        ControlledExternalSearchService(ListProvider()).search("RAII", "RAII")

    excessive = tuple(
        _candidate(url=f"https://docs{i}.example.com/raii")
        for i in range(26)
    )
    with pytest.raises(ExternalSearchUnavailableError, match="too many"):
        ControlledExternalSearchService(
            SyntheticProvider(excessive)
        ).search("RAII", "RAII")


@pytest.mark.parametrize(
    "question",
    ("", "   ", "x" * 481, "bad\0query"),
)
def test_preview_rejects_invalid_queries(question: str) -> None:
    """直接调用应用服务也不能绕过 HTTP 的文本上限。"""
    with pytest.raises(ValueError):
        ControlledExternalSearchService().preview(question)
