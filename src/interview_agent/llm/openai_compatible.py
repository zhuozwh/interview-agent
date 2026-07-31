"""使用 httpx 调用 OpenAI-compatible 非流式 Chat Completions API。"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from numbers import Real
from urllib.parse import urlsplit

import httpx

from interview_agent.llm.models import (
    ChatMessage,
    ChatRole,
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMInputError,
    LLMRateLimitError,
    LLMRequestError,
    LLMResponse,
    LLMResponseError,
    LLMServiceError,
    LLMTimeoutError,
    LLMUsage,
)

DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-v4-flash"
DEFAULT_LLM_TIMEOUT_SECONDS = 60.0
DEFAULT_LLM_MAX_RETRIES = 2
DEFAULT_LLM_TEMPERATURE = 0.2
DEFAULT_LLM_MAX_TOKENS = 1_200

MAX_MESSAGE_COUNT = 64
MAX_MESSAGE_CHARACTERS = 50_000
MAX_TOTAL_MESSAGE_CHARACTERS = 100_000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_API_KEY_CHARACTERS = 4_096
MAX_RETRY_DELAY_SECONDS = 5.0
MAX_REPORTED_TOKEN_COUNT = 10_000_000

_SAFE_RETRY_STATUS_CODES = {429, 502, 503, 504}
_ALLOWED_FINISH_REASONS = {
    "stop",
    "length",
    "content_filter",
    "insufficient_system_resource",
}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class OpenAICompatibleLLMClient:
    """最小同步适配器；不负责提示词、路由、Tool 或回答业务规则。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_LLM_BASE_URL,
        model: str = DEFAULT_LLM_MODEL,
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_LLM_MAX_RETRIES,
        temperature: float = DEFAULT_LLM_TEMPERATURE,
        max_tokens: int = DEFAULT_LLM_MAX_TOKENS,
        http_client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = _validate_base_url(base_url)
        self.model = _require_safe_non_empty(model, "model", max_length=256)
        self._api_key = _validate_api_key(api_key)
        self.timeout_seconds = _validate_timeout(timeout_seconds)
        self.max_retries = _validate_max_retries(max_retries)
        self.temperature = _validate_temperature(temperature)
        self.max_tokens = _validate_max_tokens(max_tokens)
        if not callable(sleeper):
            raise LLMConfigurationError("sleeper must be callable")
        self._sleeper = sleeper
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.Client(
            follow_redirects=False,
            trust_env=False,
        )

    def __repr__(self) -> str:
        """调试信息只展示非敏感配置，绝不包含 API 密钥。"""
        return (
            f"{type(self).__name__}("
            f"base_url={self.base_url!r}, "
            f"model={self.model!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"max_retries={self.max_retries!r})"
        )

    def __enter__(self) -> OpenAICompatibleLLMClient:
        """允许用 with 明确管理内部 httpx 连接池。"""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        """只关闭适配器自己创建的客户端，不接管外部注入对象。"""
        if self._owns_http_client:
            self._http_client.close()

    def complete(self, messages: tuple[ChatMessage, ...]) -> LLMResponse:
        """发送一次非流式请求，并对安全可重试失败执行有限退避。"""
        normalized_messages = _validate_messages(messages)
        payload = {
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in normalized_messages
            ],
            "model": self.model,
            "stream": False,
            "temperature": self.temperature,
        }
        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(self.max_retries + 1):
            try:
                response = self._http_client.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                )
            except (httpx.ConnectTimeout, httpx.ConnectError) as error:
                if attempt < self.max_retries:
                    self._sleep_before_retry(attempt, retry_after=None)
                    continue
                if isinstance(error, httpx.ConnectTimeout):
                    raise LLMTimeoutError(
                        "The LLM connection attempt timed out."
                    ) from None
                raise LLMConnectionError(
                    "The LLM service could not be reached."
                ) from None
            except httpx.TimeoutException:
                # 读取或写入超时可能发生在供应方已经开始计费之后，自动重试会
                # 产生重复请求；因此只返回可重试状态，由上层显式决定。
                raise LLMTimeoutError(
                    "The LLM request timed out after it may have been accepted."
                ) from None
            except httpx.TransportError:
                raise LLMConnectionError(
                    "The LLM connection failed after the request started."
                ) from None

            if (
                response.status_code in _SAFE_RETRY_STATUS_CODES
                and attempt < self.max_retries
            ):
                self._sleep_before_retry(
                    attempt,
                    retry_after=response.headers.get("Retry-After"),
                )
                continue
            _raise_for_http_status(response.status_code)
            return _parse_response(response)

        # 循环的所有分支都会返回或抛错，这里只用于防止未来修改静默漏出。
        raise LLMServiceError("The LLM request exhausted its retry policy.")

    def _sleep_before_retry(
        self,
        attempt: int,
        *,
        retry_after: str | None,
    ) -> None:
        """优先使用有界 Retry-After，否则采用短指数退避。"""
        delay = _parse_retry_after(retry_after)
        if delay is None:
            delay = min(0.25 * (2**attempt), MAX_RETRY_DELAY_SECONDS)
        self._sleeper(delay)


def _validate_messages(messages: object) -> tuple[ChatMessage, ...]:
    """限制消息数量、角色和正文规模，避免无界或非法请求离开本机。"""
    if not isinstance(messages, tuple):
        raise LLMInputError("messages must be a tuple")
    if not 1 <= len(messages) <= MAX_MESSAGE_COUNT:
        raise LLMInputError(
            f"messages must contain between 1 and {MAX_MESSAGE_COUNT} items"
        )

    total_characters = 0
    for message in messages:
        if not isinstance(message, ChatMessage):
            raise LLMInputError("messages must contain ChatMessage values")
        if not isinstance(message.role, ChatRole):
            raise LLMInputError("message role must be a ChatRole")
        if (
            not isinstance(message.content, str)
            or not message.content.strip()
            or "\0" in message.content
            or not _is_valid_utf8(message.content)
        ):
            raise LLMInputError(
                "message content must be non-empty valid UTF-8 text without NUL"
            )
        if len(message.content) > MAX_MESSAGE_CHARACTERS:
            raise LLMInputError(
                f"message content must not exceed {MAX_MESSAGE_CHARACTERS} "
                "characters"
            )
        total_characters += len(message.content)
    if total_characters > MAX_TOTAL_MESSAGE_CHARACTERS:
        raise LLMInputError(
            "total message content exceeds the configured safety boundary"
        )
    return messages


def _parse_response(response: httpx.Response) -> LLMResponse:
    """严格解析供应方响应，不把原始响应正文放进异常。"""
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as error:
            raise LLMResponseError(
                "The LLM response has an invalid Content-Length header."
            ) from error
        if declared_length < 0 or declared_length > MAX_RESPONSE_BYTES:
            raise LLMResponseError(
                "The LLM response exceeds the local size boundary."
            )
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise LLMResponseError(
            "The LLM response exceeds the local size boundary."
        )

    invalid_json = False
    try:
        payload = json.loads(response.content)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        # 不保留 JSONDecodeError：它的 doc 属性可能包含供应方回显的完整提示词。
        payload = None
        invalid_json = True
    if invalid_json:
        raise LLMResponseError(
            "The LLM response is not valid UTF-8 JSON."
        )
    if not isinstance(payload, dict):
        raise LLMResponseError("The LLM response root must be an object.")

    request_id = _response_string(payload.get("id"), "id", max_length=256)
    model = _response_string(payload.get("model"), "model", max_length=256)
    system_fingerprint_value = payload.get("system_fingerprint")
    system_fingerprint = (
        None
        if system_fingerprint_value is None
        else _response_string(
            system_fingerprint_value,
            "system_fingerprint",
            max_length=256,
        )
    )

    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise LLMResponseError(
            "The LLM response must contain exactly one choice."
        )
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("index") != 0:
        raise LLMResponseError("The LLM response choice is invalid.")
    finish_reason = _response_string(
        choice.get("finish_reason"),
        "finish_reason",
        max_length=64,
    )
    if finish_reason == "insufficient_system_resource":
        raise LLMServiceError(
            "The LLM service stopped because resources were unavailable."
        )
    if finish_reason not in _ALLOWED_FINISH_REASONS:
        raise LLMResponseError(
            "The LLM response uses an unsupported finish reason."
        )

    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise LLMResponseError(
            "The LLM response choice must contain an assistant message."
        )
    content = _response_string(
        message.get("content"),
        "message content",
        max_length=MAX_MESSAGE_CHARACTERS,
        allow_multiline=True,
    )
    usage = _parse_usage(payload.get("usage"))
    return LLMResponse(
        request_id=request_id,
        model=model,
        content=content,
        finish_reason=finish_reason,
        system_fingerprint=system_fingerprint,
        usage=usage,
    )


def _parse_usage(value: object) -> LLMUsage:
    """解析通用 token 计数，并兼容 DeepSeek 可选缓存与推理明细。"""
    if not isinstance(value, dict):
        raise LLMResponseError("The LLM response usage must be an object.")
    prompt_tokens = _non_negative_int(value.get("prompt_tokens"), "prompt_tokens")
    completion_tokens = _non_negative_int(
        value.get("completion_tokens"),
        "completion_tokens",
    )
    total_tokens = _non_negative_int(value.get("total_tokens"), "total_tokens")
    if total_tokens < prompt_tokens + completion_tokens:
        raise LLMResponseError(
            "The LLM response total_tokens is inconsistent."
        )

    details = value.get("completion_tokens_details")
    if details is None:
        reasoning_tokens = 0
    elif isinstance(details, dict):
        reasoning_tokens = _optional_non_negative_int(
            details.get("reasoning_tokens"),
            "reasoning_tokens",
        )
    else:
        raise LLMResponseError(
            "The LLM response completion token details are invalid."
        )
    return LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_cache_hit_tokens=_optional_non_negative_int(
            value.get("prompt_cache_hit_tokens"),
            "prompt_cache_hit_tokens",
        ),
        prompt_cache_miss_tokens=_optional_non_negative_int(
            value.get("prompt_cache_miss_tokens"),
            "prompt_cache_miss_tokens",
        ),
        reasoning_tokens=reasoning_tokens,
    )


def _raise_for_http_status(status_code: int) -> None:
    """把 HTTP 状态映射为稳定错误，不读取或回显供应方错误正文。"""
    if 200 <= status_code < 300:
        return
    if status_code in {401, 403}:
        raise LLMAuthenticationError(
            "The LLM API key is invalid or unauthorized.",
            status_code=status_code,
        )
    if status_code == 429:
        raise LLMRateLimitError(
            "The LLM service rate limit was reached.",
            status_code=status_code,
        )
    if 400 <= status_code < 500:
        raise LLMRequestError(
            "The LLM service rejected the request.",
            status_code=status_code,
        )
    if 500 <= status_code < 600:
        raise LLMServiceError(
            "The LLM service returned a server error.",
            status_code=status_code,
        )
    raise LLMServiceError(
        "The LLM service returned an unexpected HTTP status.",
        status_code=status_code,
    )


def _validate_base_url(value: object) -> str:
    """远程密钥只允许发往 HTTPS；HTTP 仅可用于本机兼容服务。"""
    if not isinstance(value, str):
        raise LLMConfigurationError("base_url must be a string")
    normalized = value.strip().rstrip("/")
    if (
        not normalized
        or len(normalized) > 2_048
        or any(character.isspace() or ord(character) == 127 for character in normalized)
        or not _is_valid_utf8(normalized)
    ):
        raise LLMConfigurationError("base_url is invalid")
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        # 访问 port 会触发 urllib 对非数字和越界端口的校验。
        parsed.port
    except ValueError as error:
        raise LLMConfigurationError("base_url is invalid") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise LLMConfigurationError(
            "base_url must be an HTTP(S) origin or path without credentials, "
            "query, or fragment"
        )
    if parsed.scheme == "http" and hostname.casefold() not in _LOOPBACK_HOSTS:
        raise LLMConfigurationError(
            "Plain HTTP base_url is allowed only for loopback hosts"
        )
    return normalized


def _validate_api_key(value: object) -> str:
    """密钥只保存在客户端私有字段，禁止空白、控制字符和异常大输入。"""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_API_KEY_CHARACTERS
        or any(not 33 <= ord(character) <= 126 for character in value)
    ):
        raise LLMConfigurationError("api_key is invalid")
    return value


def _validate_timeout(value: object) -> float:
    """每次 HTTP 尝试必须有 1 到 600 秒的有限超时。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        or not 1.0 <= float(value) <= 600.0
    ):
        raise LLMConfigurationError(
            "timeout_seconds must be between 1 and 600"
        )
    return float(value)


def _validate_max_retries(value: object) -> int:
    """自动重试次数保持很小，避免放大费用和供应方故障。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 3
    ):
        raise LLMConfigurationError("max_retries must be between 0 and 3")
    return value


def _validate_temperature(value: object) -> float:
    """温度遵循 Chat Completions 的 0 到 2 范围。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 2.0
    ):
        raise LLMConfigurationError("temperature must be between 0 and 2")
    return float(value)


def _validate_max_tokens(value: object) -> int:
    """MVP 默认限制输出规模，同时允许按需配置较长回答。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 32_768
    ):
        raise LLMConfigurationError("max_tokens must be between 1 and 32768")
    return value


def _require_safe_non_empty(
    value: object,
    label: str,
    *,
    max_length: int,
) -> str:
    """配置文本不得包含空白边界、控制字符或无效 Unicode。"""
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or not _is_valid_utf8(value)
    ):
        raise LLMConfigurationError(f"{label} is invalid")
    return value


def _response_string(
    value: object,
    label: str,
    *,
    max_length: int,
    allow_multiline: bool = False,
) -> str:
    """供应方文本必须有效且有界，错误中不回显具体内容。"""
    allowed_controls = {"\t", "\n", "\r"} if allow_multiline else set()
    if (
        not isinstance(value, str)
        or not value.strip()
        or (not allow_multiline and value != value.strip())
        or len(value) > max_length
        or any(
            (ord(character) < 32 or ord(character) == 127)
            and character not in allowed_controls
            for character in value
        )
        or not _is_valid_utf8(value)
    ):
        raise LLMResponseError(f"The LLM response {label} is invalid.")
    return value


def _non_negative_int(value: object, label: str) -> int:
    """必需 token 计数只接受非负整数，布尔值不算整数。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_REPORTED_TOKEN_COUNT
    ):
        raise LLMResponseError(
            f"The LLM response {label} must be a bounded non-negative integer."
        )
    return value


def _optional_non_negative_int(value: object, label: str) -> int:
    """供应方省略可选 token 明细时统一返回 0。"""
    if value is None:
        return 0
    return _non_negative_int(value, label)


def _parse_retry_after(value: str | None) -> float | None:
    """只接受 0 到 5 秒的数字 Retry-After，拒绝异常长阻塞。"""
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        return None
    if not math.isfinite(delay) or not 0.0 <= delay <= MAX_RETRY_DELAY_SECONDS:
        return None
    return delay


def _is_valid_utf8(value: str) -> bool:
    """拒绝孤立代理字符，保证 HTTP JSON 可以编码为 UTF-8。"""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
