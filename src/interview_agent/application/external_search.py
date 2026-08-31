"""组织显式确认、默认离线的外部证据搜索用例。"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from typing import Protocol
from urllib.parse import SplitResult, parse_qsl, urlsplit, urlunsplit
from uuid import uuid4

from interview_agent.core.privacy import redact_common_personal_data
from interview_agent.core.query_policy import (
    assess_pre_retrieval_policy,
    infer_query_namespace,
)

MAX_EXTERNAL_QUERY_CHARACTERS = 480
MAX_EXTERNAL_RESULTS = 5
MAX_EXTERNAL_PROVIDER_RESULTS = 25
MAX_EXTERNAL_TITLE_CHARACTERS = 240
MAX_EXTERNAL_SNIPPET_CHARACTERS = 900
MAX_EXTERNAL_URL_CHARACTERS = 2_048

_SEARCH_PREFIX = re.compile(
    r"^(?:(?:请|麻烦)?(?:帮我)?(?:联网|上网)?"
    r"(?:搜索|查找|查询|检索|查一下)(?:一下|相关资料|相关内容)?"
    r"[：:，,、\s]*)+",
    re.IGNORECASE,
)
_SAFE_PROVIDER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,63}")
_SAFE_SOURCE_TYPES = {
    "official_documentation",
    "standard",
    "paper",
    "official_project",
    "web",
}
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]{8,}\b|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b|"
    r"\bAKIA[A-Z0-9]{16}\b|"
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b|"
    r"\b(?:api[_ -]?key|access[_ -]?token|token|secret)"
    r"\s*[:=]\s*[^\s,，;；]+|"
    r"\b(?:client[_ -]?secret|password|passwd|authorization)"
    r"\s*[:=]\s*[^\s,，;；]+)"
)
_PERSONAL_CONTEXT_MARKERS = (
    "我的",
    "本人",
    "我们",
    "咱们",
    "我叫",
    "我是",
    "我在",
    "我曾",
    "我有",
    "我做",
    "我负责",
    "我参加",
    "我投递",
    "我面试",
)
_LOCAL_REFERENCE_MARKERS = (
    "上述",
    "上面",
    "前面",
    "刚才",
    "这份资料",
    "这个回答",
    "本地资料",
    "知识库",
)
_SENSITIVE_URL_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "code",
    "client_secret",
    "key",
    "password",
    "passwd",
    "secret",
    "session",
    "sig",
    "signature",
    "token",
}


class ExternalSearchPolicyRefusedError(PermissionError):
    """问题包含只能留在本地处理的内容。"""

    def __init__(self, reason_code: str, safe_query: str | None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.safe_query = safe_query


class ExternalSearchConfirmationError(RuntimeError):
    """确认文本与当前服务端预览不一致。"""


class ExternalSearchUnavailableError(RuntimeError):
    """外部搜索提供方尚未配置或未能安全完成。"""


@dataclass(frozen=True, slots=True)
class ExternalSearchPreview:
    """用户确认前可以安全展示的查询与策略结果。"""

    allowed: bool
    reason_code: str
    query: str | None
    provider_configured: bool
    provider_name: str | None
    max_results: int


@dataclass(frozen=True, slots=True)
class ExternalSearchCandidate:
    """提供方返回的未信任候选；生产适配器也必须使用该协议。"""

    title: str
    url: str
    snippet: str
    source_type: str = "web"


@dataclass(frozen=True, slots=True)
class ExternalSource:
    """通过确定性校验后可展示的临时外部来源。"""

    citation_id: str
    title: str
    url: str
    display_domain: str
    snippet: str
    source_type: str


@dataclass(frozen=True, slots=True)
class ExternalSearchResult:
    """一次显式搜索的临时结果，不进入聊天历史。"""

    search_id: str
    query: str
    provider_name: str
    sources: tuple[ExternalSource, ...]


class ExternalSearchProvider(Protocol):
    """v0.5.5 可实现的单一搜索提供方边界。"""

    @property
    def name(self) -> str:
        """返回公开、稳定且不含密钥的提供方名称。"""

    def search(
        self,
        query: str,
        *,
        max_results: int,
    ) -> tuple[ExternalSearchCandidate, ...]:
        """执行一次有界搜索；不得在实现内部自动重试。"""


class ExternalSearchService(Protocol):
    """HTTP 层依赖的预览和显式搜索用例。"""

    def preview(self, question: str) -> ExternalSearchPreview:
        """本地计算实际将外发的查询，不调用提供方。"""

    def search(
        self,
        question: str,
        confirmed_query: str,
    ) -> ExternalSearchResult:
        """确认匹配后最多调用提供方一次。"""


class ControlledExternalSearchService:
    """只允许普通技术知识查询进入可注入的外部提供方。"""

    def __init__(
        self,
        provider: ExternalSearchProvider | None = None,
        *,
        max_results: int = MAX_EXTERNAL_RESULTS,
    ) -> None:
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or not 1 <= max_results <= MAX_EXTERNAL_RESULTS
        ):
            raise ValueError("max_results is outside the supported range")
        self.provider = provider
        self.max_results = max_results
        self._provider_name = _provider_name(provider)

    def preview(self, question: str) -> ExternalSearchPreview:
        """脱敏、归一化并阻止个人资料问题进入外部边界。"""
        normalized = _normalize_question(question)
        personal_data_redacted = redact_common_personal_data(normalized)
        redacted = _SECRET_VALUE_PATTERN.sub(
            "[REDACTED_SECRET]",
            personal_data_redacted,
        )
        query = _SEARCH_PREFIX.sub("", redacted).strip(" ：:，,、")
        if not query:
            raise ValueError("question does not contain a searchable query")
        provider_configured = self.provider is not None
        if redacted != normalized:
            return ExternalSearchPreview(
                allowed=False,
                reason_code="sensitive_data_detected",
                query=query,
                provider_configured=provider_configured,
                provider_name=self._provider_name,
                max_results=self.max_results,
            )
        policy = assess_pre_retrieval_policy(normalized)
        if not policy.allowed:
            return ExternalSearchPreview(
                allowed=False,
                reason_code=policy.reason_code,
                query=query,
                provider_configured=provider_configured,
                provider_name=self._provider_name,
                max_results=self.max_results,
            )
        if any(
            marker in normalized
            for marker in (*_PERSONAL_CONTEXT_MARKERS, *_LOCAL_REFERENCE_MARKERS)
        ):
            return ExternalSearchPreview(
                allowed=False,
                reason_code="personal_context_requires_local_evidence",
                query=query,
                provider_configured=provider_configured,
                provider_name=self._provider_name,
                max_results=self.max_results,
            )
        if infer_query_namespace(normalized) != "notes":
            return ExternalSearchPreview(
                allowed=False,
                reason_code="personal_context_requires_local_evidence",
                query=query,
                provider_configured=provider_configured,
                provider_name=self._provider_name,
                max_results=self.max_results,
            )
        return ExternalSearchPreview(
            allowed=True,
            reason_code="query_allowed",
            query=query,
            provider_configured=provider_configured,
            provider_name=self._provider_name,
            max_results=self.max_results,
        )

    def search(
        self,
        question: str,
        confirmed_query: str,
    ) -> ExternalSearchResult:
        """重新计算策略，拒绝篡改确认，并且只调用提供方一次。"""
        preview = self.preview(question)
        if not preview.allowed:
            raise ExternalSearchPolicyRefusedError(
                preview.reason_code,
                preview.query,
            )
        if (
            not isinstance(confirmed_query, str)
            or confirmed_query != preview.query
        ):
            raise ExternalSearchConfirmationError(
                "Confirmed query does not match the current preview."
            )
        if self.provider is None or self._provider_name is None:
            raise ExternalSearchUnavailableError(
                "External search provider is not configured."
            )
        try:
            candidates = self.provider.search(
                preview.query,
                max_results=self.max_results,
            )
        except Exception as error:
            raise ExternalSearchUnavailableError(
                "External search provider could not complete the request."
            ) from error
        sources = _normalize_sources(candidates, self.max_results)
        return ExternalSearchResult(
            search_id=str(uuid4()),
            query=preview.query,
            provider_name=self._provider_name,
            sources=sources,
        )


def _normalize_question(question: object) -> str:
    """外部预览不接受空文本、控制字符或超出聊天输入上限的内容。"""
    if (
        not isinstance(question, str)
        or not question.strip()
        or len(question) > MAX_EXTERNAL_QUERY_CHARACTERS
        or "\0" in question
    ):
        raise ValueError("question must be safe bounded text")
    try:
        question.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("question must be valid UTF-8") from error
    return " ".join(question.split())


def _provider_name(provider: ExternalSearchProvider | None) -> str | None:
    """提供方名称进入界面前必须是短而公开的标识。"""
    if provider is None:
        return None
    name = provider.name
    if not isinstance(name, str) or _SAFE_PROVIDER_NAME.fullmatch(name) is None:
        raise ValueError("provider name is invalid")
    return name


def _normalize_sources(
    candidates: object,
    max_results: int,
) -> tuple[ExternalSource, ...]:
    """限制候选集合，过滤危险 URL，并按规范 URL 去重。"""
    if not isinstance(candidates, tuple):
        raise ExternalSearchUnavailableError(
            "External search provider returned an invalid collection."
        )
    if len(candidates) > MAX_EXTERNAL_PROVIDER_RESULTS:
        raise ExternalSearchUnavailableError(
            "External search provider returned too many results."
        )
    sources: list[ExternalSource] = []
    seen_urls: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, ExternalSearchCandidate):
            raise ExternalSearchUnavailableError(
                "External search provider returned an invalid result."
            )
        normalized_url = _public_https_url(candidate.url)
        if normalized_url is None or normalized_url in seen_urls:
            continue
        title = _bounded_external_text(
            candidate.title,
            MAX_EXTERNAL_TITLE_CHARACTERS,
        )
        snippet = _bounded_external_text(
            candidate.snippet,
            MAX_EXTERNAL_SNIPPET_CHARACTERS,
        )
        source_type = (
            candidate.source_type
            if candidate.source_type in _SAFE_SOURCE_TYPES
            else "web"
        )
        if title is None or snippet is None:
            continue
        seen_urls.add(normalized_url)
        hostname = urlsplit(normalized_url).hostname
        if hostname is None:
            continue
        sources.append(
            ExternalSource(
                citation_id=f"W{len(sources) + 1}",
                title=title,
                url=normalized_url,
                display_domain=hostname.casefold(),
                snippet=snippet,
                source_type=source_type,
            )
        )
        if len(sources) >= max_results:
            break
    return tuple(sources)


def _bounded_external_text(value: object, maximum: int) -> str | None:
    """把网页标题和摘要压成有界纯文本，控制字符不进入界面。"""
    if not isinstance(value, str) or "\0" in value:
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return normalized[:maximum]


def _public_https_url(value: object) -> str | None:
    """只允许无凭据、无自定义端口且不是本机地址的 HTTPS URL。"""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_EXTERNAL_URL_CHARACTERS
        or "\0" in value
        or "\\" in value
        or any(ord(character) < 0x21 or ord(character) == 0x7F for character in value)
    ):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    hostname = parsed.hostname
    normalized_hostname = (
        _normalize_public_hostname(hostname) if hostname is not None else None
    )
    try:
        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            max_num_fields=50,
        )
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or normalized_hostname is None
        or any(
            key.casefold() in _SENSITIVE_URL_QUERY_KEYS
            for key, _ in query_pairs
        )
    ):
        return None
    normalized = SplitResult(
        scheme="https",
        netloc=normalized_hostname,
        path=parsed.path or "/",
        query=parsed.query,
        fragment="",
    )
    return urlunsplit(normalized)


def _normalize_public_hostname(hostname: str) -> str | None:
    """不做 DNS 请求，只返回规范公共域名并拒绝所有字面 IP。"""
    try:
        normalized = hostname.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError:
        return None
    if (
        len(normalized) > 253
        or normalized in {"localhost", "localhost.localdomain"}
        or normalized.endswith((".localhost", ".local", ".internal"))
        or "." not in normalized
    ):
        return None
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        return normalized if all(
            label
            and len(label) <= 63
            and label[0] != "-"
            and label[-1] != "-"
            and all(character.isalnum() or character == "-" for character in label)
            for label in normalized.split(".")
        ) else None
    return None


__all__ = [
    "ControlledExternalSearchService",
    "ExternalSearchCandidate",
    "ExternalSearchConfirmationError",
    "ExternalSearchPolicyRefusedError",
    "ExternalSearchPreview",
    "ExternalSearchProvider",
    "ExternalSearchResult",
    "ExternalSearchService",
    "ExternalSearchUnavailableError",
    "ExternalSource",
    "MAX_EXTERNAL_QUERY_CHARACTERS",
    "MAX_EXTERNAL_RESULTS",
]
