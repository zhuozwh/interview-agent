"""验证进入远端模型前的通用个人数据脱敏边界。"""

from interview_agent.core.privacy import redact_common_personal_data


def test_redaction_removes_local_absolute_paths() -> None:
    """不同平台的本机路径和 file URI 都不能进入模型上下文。"""
    sensitive_paths = (
        "E:/private/resume/candidate.docx",
        r"C:\Users\candidate\resume.md",
        r"\\server\private\candidate.docx",
        "/home/candidate/resume.md",
        "/Users/candidate/resume.md",
        "/root/private/resume.md",
        "file:///E:/private/resume/candidate.docx",
    )
    content = "\n".join(
        (
            f'source_file: "{sensitive_paths[0]}"',
            f"windows: {sensitive_paths[1]}",
            f"unc: {sensitive_paths[2]}",
            f"linux: {sensitive_paths[3]}",
            f"mac: {sensitive_paths[4]}",
            f"root: {sensitive_paths[5]}",
            f"[打开原件](<{sensitive_paths[6]}>)",
        )
    )

    redacted = redact_common_personal_data(content)

    assert all(path not in redacted for path in sensitive_paths)
    assert redacted.count("[REDACTED_LOCAL_PATH]") == len(sensitive_paths)


def test_redaction_preserves_web_urls_and_relative_application_routes() -> None:
    """隐私过滤不能误删普通网页地址和应用相对路由。"""
    content = (
        "文档：https://example.com/home/candidate/resume；"
        "接口：/ask；文件：docs/resume.md"
    )

    assert redact_common_personal_data(content) == content


def test_redaction_is_idempotent_for_local_path_placeholder() -> None:
    """重复执行脱敏不能继续改变已经安全的占位符。"""
    content = "原件：[REDACTED_LOCAL_PATH]"

    assert redact_common_personal_data(content) == content


def test_redaction_does_not_treat_hex_fingerprint_as_identity_number() -> None:
    """片段指纹中的连续数字不能被误判为身份证号。"""
    fingerprint = (
        "f397422542963088856d2257a4f6e8c8a7429dd895e66687838673cbeb69fdf7"
    )

    assert redact_common_personal_data(fingerprint) == fingerprint
