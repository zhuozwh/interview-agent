"""定义业务层可依赖、与具体 LLM 供应方无关的最小协议。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ChatRole(StrEnum):
    """当前非 Tool-Calling 对话允许的消息角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """发送给 LLM 的一条纯文本消息。"""

    role: ChatRole
    content: str


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """供应方返回的 token 用量；缓存和推理明细缺失时记为 0。"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """一次非流式文本补全的稳定返回结构。"""

    request_id: str
    model: str
    content: str
    finish_reason: str
    system_fingerprint: str | None
    usage: LLMUsage


class LLMClient(Protocol):
    """Agent 生成回答只依赖这一条同步补全能力。"""

    def complete(self, messages: tuple[ChatMessage, ...]) -> LLMResponse:
        """根据已组装消息返回一条经过结构校验的回答。"""


class LLMError(RuntimeError):
    """不会携带密钥、提示词或供应方响应正文的稳定错误基类。"""

    code = "llm_error"
    retryable = False

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMConfigurationError(LLMError, ValueError):
    """客户端配置无法安全发起请求。"""

    code = "llm_configuration_error"


class LLMInputError(LLMError, ValueError):
    """消息或生成参数不符合本地边界。"""

    code = "llm_invalid_input"


class LLMTimeoutError(LLMError, TimeoutError):
    """请求在供应方返回前超过超时。"""

    code = "llm_timeout"
    retryable = True


class LLMConnectionError(LLMError, ConnectionError):
    """无法建立或维持到供应方的连接。"""

    code = "llm_connection_failed"
    retryable = True


class LLMAuthenticationError(LLMError):
    """API 密钥无效或没有调用权限。"""

    code = "llm_authentication_failed"


class LLMRateLimitError(LLMError):
    """供应方拒绝当前并发或速率。"""

    code = "llm_rate_limited"
    retryable = True


class LLMRequestError(LLMError):
    """供应方认为请求无效，自动重试不会改变结果。"""

    code = "llm_request_rejected"


class LLMServiceError(LLMError):
    """供应方暂时不可用或返回服务端错误。"""

    code = "llm_service_unavailable"
    retryable = True


class LLMResponseError(LLMError):
    """供应方成功响应不满足本地协议。"""

    code = "llm_invalid_response"
    retryable = True
