"""验证 OpenAI-compatible LLM 适配器的协议、重试和数据安全边界。"""

from __future__ import annotations

import json

import httpx
import pytest

from interview_agent.llm import (
    ChatMessage,
    ChatRole,
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMInputError,
    LLMRateLimitError,
    LLMRequestError,
    LLMResponseError,
    LLMServiceError,
    LLMTimeoutError,
    OpenAICompatibleLLMClient,
)

_API_KEY = "test-api-key-that-must-not-leak"


def _messages() -> tuple[ChatMessage, ...]:
    """返回不含个人数据的最小测试消息。"""
    return (
        ChatMessage(role=ChatRole.SYSTEM, content="只回答技术问题。"),
        ChatMessage(role=ChatRole.USER, content="什么是 RAII？"),
    )


def _success_payload() -> dict[str, object]:
    """生成字段完整的 DeepSeek/OpenAI-compatible 成功响应。"""
    return {
        "id": "request-123",
        "model": "deepseek-v4-flash",
        "system_fingerprint": "fp-test",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "RAII 使用对象生命周期管理资源。",
                },
            }
        ],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
            "prompt_cache_hit_tokens": 5,
            "prompt_cache_miss_tokens": 15,
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
    }


def _client(
    handler,
    *,
    max_retries: int = 2,
    sleeper=lambda seconds: None,
) -> tuple[OpenAICompatibleLLMClient, httpx.Client]:
    """使用内存 Transport 创建不会访问网络的客户端。"""
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleLLMClient(
        api_key=_API_KEY,
        http_client=http_client,
        max_retries=max_retries,
        sleeper=sleeper,
    )
    return client, http_client


def test_success_sends_minimal_request_and_parses_usage() -> None:
    """只发送兼容字段，返回正文、停止原因和 token 明细。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_payload())

    client, http_client = _client(handler)
    try:
        response = client.complete(_messages())
    finally:
        http_client.close()

    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {_API_KEY}"
    payload = json.loads(request.content)
    assert payload == {
        "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": "只回答技术问题。"},
            {"role": "user", "content": "什么是 RAII？"},
        ],
        "model": "deepseek-v4-flash",
        "stream": False,
        "temperature": 0.2,
    }
    assert response.request_id == "request-123"
    assert response.content == "RAII 使用对象生命周期管理资源。"
    assert response.finish_reason == "stop"
    assert response.system_fingerprint == "fp-test"
    assert response.usage.prompt_tokens == 20
    assert response.usage.prompt_cache_hit_tokens == 5
    assert response.usage.reasoning_tokens == 3
    assert _API_KEY not in repr(client)


@pytest.mark.parametrize("thinking_mode", ["enabled", "disabled"])
def test_explicit_thinking_mode_uses_bounded_provider_extension(
    thinking_mode: str,
) -> None:
    """显式模式只生成固定 thinking 结构，不开放任意 extra_body。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_success_payload())

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleLLMClient(
        api_key=_API_KEY,
        thinking_mode=thinking_mode,
        http_client=http_client,
    )
    try:
        client.complete(_messages())
    finally:
        http_client.close()

    payload = json.loads(captured[0].content)
    assert payload["thinking"] == {"type": thinking_mode}


def test_retries_only_safe_statuses_with_bounded_delay() -> None:
    """429、502/503/504 可有限重试，并尊重不超过五秒的数字 Retry-After。"""
    statuses = [429, 503, 200]
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        if status == 429:
            return httpx.Response(status, headers={"Retry-After": "0.1"})
        if status == 503:
            return httpx.Response(status)
        return httpx.Response(status, json=_success_payload())

    client, http_client = _client(handler, sleeper=sleeps.append)
    try:
        response = client.complete(_messages())
    finally:
        http_client.close()

    assert response.content.startswith("RAII")
    assert statuses == []
    assert sleeps == [0.1, 0.5]


def test_connect_failure_retries_but_read_timeout_does_not() -> None:
    """连接未建立可以重试；可能已计费的读取超时不能自动重复 POST。"""
    connect_calls = 0

    def connect_handler(request: httpx.Request) -> httpx.Response:
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            raise httpx.ConnectError("simulated", request=request)
        return httpx.Response(200, json=_success_payload())

    client, http_client = _client(connect_handler)
    try:
        assert client.complete(_messages()).content.startswith("RAII")
    finally:
        http_client.close()
    assert connect_calls == 2

    read_calls = 0

    def read_timeout_handler(request: httpx.Request) -> httpx.Response:
        nonlocal read_calls
        read_calls += 1
        raise httpx.ReadTimeout("simulated", request=request)

    client, http_client = _client(read_timeout_handler)
    try:
        with pytest.raises(LLMTimeoutError, match="may have been accepted"):
            client.complete(_messages())
    finally:
        http_client.close()
    assert read_calls == 1


def test_non_retryable_and_exhausted_http_errors_are_stable() -> None:
    """认证、请求、500 和耗尽的 429 不暴露供应方错误正文。"""
    cases = [
        (401, LLMAuthenticationError),
        (400, LLMRequestError),
        (500, LLMServiceError),
    ]
    for status, error_type in cases:
        calls = 0

        def handler(request: httpx.Request, response_status=status):
            nonlocal calls
            calls += 1
            return httpx.Response(
                response_status,
                text=f"sensitive provider body {_API_KEY}",
            )

        client, http_client = _client(handler)
        try:
            with pytest.raises(error_type) as caught:
                client.complete(_messages())
        finally:
            http_client.close()
        assert calls == 1
        assert _API_KEY not in str(caught.value)
        assert caught.value.status_code == status

    rate_calls = 0

    def rate_handler(request: httpx.Request) -> httpx.Response:
        nonlocal rate_calls
        rate_calls += 1
        return httpx.Response(429)

    client, http_client = _client(rate_handler)
    try:
        with pytest.raises(LLMRateLimitError) as caught:
            client.complete(_messages())
    finally:
        http_client.close()
    assert rate_calls == 3
    assert caught.value.retryable is True


@pytest.mark.parametrize(
    "messages",
    [
        [],
        (),
        (ChatMessage(role=ChatRole.USER, content=" "),),
        (ChatMessage(role=ChatRole.USER, content="bad\0content"),),
        (ChatMessage(role=ChatRole.USER, content="\ud800"),),
        (ChatMessage(role=ChatRole.USER, content="x" * 50_001),),
        tuple(
            ChatMessage(role=ChatRole.USER, content=str(index))
            for index in range(65)
        ),
    ],
)
def test_rejects_invalid_messages_before_http(messages) -> None:
    """无效或无界消息不会离开本机，也不会进入重试流程。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP must not be called")

    client, http_client = _client(handler)
    try:
        with pytest.raises(LLMInputError):
            client.complete(messages)
    finally:
        http_client.close()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"api_key": "sensitive invalid key"}, "api_key"),
        ({"api_key": "密钥"}, "api_key"),
        ({"base_url": "http://api.example.com"}, "loopback"),
        ({"base_url": "https://user@example.com"}, "without credentials"),
        ({"base_url": "https://api.example.com?x=1"}, "without credentials"),
        ({"base_url": "https://example.com:bad"}, "base_url"),
        ({"base_url": "https://[::1"}, "base_url"),
        ({"base_url": "https://exa mple.com"}, "base_url"),
        ({"model": " bad "}, "model"),
        ({"thinking_mode": "automatic"}, "provider_default"),
        ({"thinking_mode": False}, "provider_default"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": float("inf")}, "timeout_seconds"),
        ({"max_retries": 4}, "max_retries"),
        ({"temperature": 2.1}, "temperature"),
        ({"max_tokens": 0}, "max_tokens"),
    ],
)
def test_rejects_unsafe_client_configuration(kwargs, message: str) -> None:
    """密钥、目标地址和费用相关参数在创建客户端时即失败。"""
    parameters = {"api_key": _API_KEY, **kwargs}
    with pytest.raises(LLMConfigurationError, match=message):
        OpenAICompatibleLLMClient(**parameters)

    # 错误信息不得回显传入的密钥值。
    if "api_key" in kwargs:
        try:
            OpenAICompatibleLLMClient(**parameters)
        except LLMConfigurationError as error:
            assert kwargs["api_key"] not in str(error)


def test_allows_plain_http_only_for_loopback_and_preserves_external_client() -> None:
    """本地兼容服务可用 HTTP；外部注入连接池的生命周期仍属于调用方。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(
            "http://127.0.0.1:8080/v1/chat/completions"
        )
        return httpx.Response(200, json=_success_payload())

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    with OpenAICompatibleLLMClient(
        api_key=_API_KEY,
        base_url="http://127.0.0.1:8080/v1/",
        http_client=http_client,
    ) as client:
        client.complete(_messages())
    assert http_client.is_closed is False
    http_client.close()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {**_success_payload(), "choices": []},
        {
            **_success_payload(),
            "choices": [
                {
                    "index": 1,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "answer"},
                }
            ],
        },
        {
            **_success_payload(),
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {"role": "assistant", "content": None},
                }
            ],
        },
        {**_success_payload(), "usage": {"prompt_tokens": 2}},
        {
            **_success_payload(),
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 5,
            },
        },
    ],
)
def test_rejects_malformed_success_payloads(payload) -> None:
    """HTTP 200 不等于业务成功，响应结构和 token 统计仍需严格校验。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client, http_client = _client(handler)
    try:
        with pytest.raises(LLMResponseError):
            client.complete(_messages())
    finally:
        http_client.close()


def test_rejects_invalid_json_oversized_body_and_resource_stop() -> None:
    """损坏、超限或资源不足的响应不会被伪装成正常回答。"""
    responses = [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(
            200,
            content=b"{}",
            headers={"Content-Length": str(2 * 1024 * 1024 + 1)},
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client, http_client = _client(handler)
    try:
        with pytest.raises(LLMResponseError, match="JSON") as invalid_json:
            client.complete(_messages())
        assert invalid_json.value.__context__ is None
        with pytest.raises(LLMResponseError, match="size boundary"):
            client.complete(_messages())
    finally:
        http_client.close()

    resource_payload = _success_payload()
    resource_payload["choices"] = [
        {
            "index": 0,
            "finish_reason": "insufficient_system_resource",
            "message": {"role": "assistant", "content": ""},
        }
    ]

    def resource_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=resource_payload)

    client, http_client = _client(resource_handler)
    try:
        with pytest.raises(LLMServiceError, match="resources"):
            client.complete(_messages())
    finally:
        http_client.close()


def test_rejects_control_characters_in_provider_metadata() -> None:
    """供应方身份字段可能进入日志和追踪，不能携带换行伪造记录。"""
    payload = _success_payload()
    payload["id"] = "request\nforged-log-line"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client, http_client = _client(handler)
    try:
        with pytest.raises(LLMResponseError, match="id"):
            client.complete(_messages())
    finally:
        http_client.close()

    payload = _success_payload()
    payload["model"] = " deepseek-v4-flash "

    def whitespace_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client, http_client = _client(whitespace_handler)
    try:
        with pytest.raises(LLMResponseError, match="model"):
            client.complete(_messages())
    finally:
        http_client.close()


def test_transport_failure_does_not_leak_low_level_message() -> None:
    """连接错误可能包含 URL 或环境细节，稳定异常不能原样传播。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"sensitive transport detail {_API_KEY}",
            request=request,
        )

    client, http_client = _client(handler, max_retries=0)
    try:
        with pytest.raises(LLMConnectionError) as caught:
            client.complete(_messages())
    finally:
        http_client.close()
    assert _API_KEY not in str(caught.value)
