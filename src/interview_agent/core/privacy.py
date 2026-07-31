"""提供进入远端 LLM 前可复用的最小文本脱敏能力。"""

import re

_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])",
    re.UNICODE,
)
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d(?:[- ]?\d){8}(?!\d)"
)
_IDENTITY_CARD_PATTERN = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_MESSAGING_ID_PATTERN = re.compile(
    r"(?i)(?P<label>微信|wechat|wx)\s*[:：]\s*[A-Za-z0-9_-]{5,64}"
)


def redact_common_personal_data(content: str) -> str:
    """移除常见邮箱、手机号、身份证号和即时通讯账号格式。"""
    if not isinstance(content, str):
        raise TypeError("content must be a string")
    redacted = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", content)
    redacted = _PHONE_PATTERN.sub("[REDACTED_PHONE]", redacted)
    redacted = _IDENTITY_CARD_PATTERN.sub("[REDACTED_ID]", redacted)
    return _MESSAGING_ID_PATTERN.sub(
        lambda match: f"{match.group('label')}：[REDACTED_ACCOUNT]",
        redacted,
    )


__all__ = ["redact_common_personal_data"]
