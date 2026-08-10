"""提供进入远端 LLM 前可复用的最小文本脱敏能力。"""

import re

_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])",
    re.UNICODE,
)
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d(?:[- ]?\d){8}(?!\d)"
)
_IDENTITY_CARD_PATTERN = re.compile(
    r"(?<![\dA-Za-z])\d{17}[\dXx](?![\dA-Za-z])"
)
_MESSAGING_ID_PATTERN = re.compile(
    r"(?i)(?P<label>微信|wechat|wx)\s*[:：]\s*[A-Za-z0-9_-]{5,64}"
)
_LOCAL_FILE_URI_PATTERN = re.compile(
    r"(?i)\bfile://[^<>\r\n\"')\]}]+"
)
_WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/])[^<>\r\n\"'|?*)\]}]+"
)
_WINDOWS_UNC_PATH_PATTERN = re.compile(
    r"(?<![\\])\\\\[^\\/\s]+[\\/][^<>\r\n\"'|?*)\]}]+"
)
_POSIX_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![\w:/])/(?:[^/\s<>\r\n\"'*)\]}:：;；,，。!?！？]+/)+"
    r"[^/\s<>\r\n\"'*)\]}:：;；,，。!?！？]+"
)


def redact_common_personal_data(content: str) -> str:
    """移除常见个人标识与可能暴露用户名的本机绝对路径。"""
    if not isinstance(content, str):
        raise TypeError("content must be a string")
    redacted = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", content)
    redacted = _PHONE_PATTERN.sub("[REDACTED_PHONE]", redacted)
    redacted = _IDENTITY_CARD_PATTERN.sub("[REDACTED_ID]", redacted)
    redacted = _MESSAGING_ID_PATTERN.sub(
        lambda match: f"{match.group('label')}：[REDACTED_ACCOUNT]",
        redacted,
    )
    # 先处理 file URI，避免其中的盘符路径只被部分替换。
    redacted = _LOCAL_FILE_URI_PATTERN.sub("[REDACTED_LOCAL_PATH]", redacted)
    redacted = _WINDOWS_ABSOLUTE_PATH_PATTERN.sub(
        "[REDACTED_LOCAL_PATH]", redacted
    )
    redacted = _WINDOWS_UNC_PATH_PATTERN.sub(
        "[REDACTED_LOCAL_PATH]", redacted
    )
    return _POSIX_ABSOLUTE_PATH_PATTERN.sub(
        "[REDACTED_LOCAL_PATH]", redacted
    )


__all__ = ["redact_common_personal_data"]
