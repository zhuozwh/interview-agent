"""验证配置默认值和环境变量覆盖行为。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from interview_agent.core.config import Settings, get_settings


def test_default_settings_load() -> None:
    # 禁用 .env，确保这个测试只验证代码中声明的默认值。
    settings = Settings(_env_file=None)

    assert settings.app_name == "Interview Agent"
    assert settings.app_env == "local"
    assert settings.log_level == "INFO"
    assert settings.database_path == Path("data/interview_agent.db")

    # Phase 1A 的默认配置只面向仓库下的 knowledge 目录，并带有安全读取上限。
    assert settings.markdown_source_directory == Path("knowledge")
    assert settings.allowed_data_directories == (Path("knowledge"),)
    assert settings.markdown_max_file_size_bytes == 2 * 1024 * 1024
    assert settings.markdown_max_total_size_bytes == 20 * 1024 * 1024


def test_environment_variables_override_settings(monkeypatch) -> None:
    # monkeypatch 创建的环境变量只在当前测试期间有效，结束后会自动恢复。
    monkeypatch.setenv("APP_NAME", "Test Interview Agent")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("DATABASE_PATH", "temporary/test.db")

    # 复杂类型使用 JSON 数组传入，模拟 .env 中允许多个数据目录的写法。
    monkeypatch.setenv("MARKDOWN_SOURCE_DIRECTORY", "temporary/notes")
    monkeypatch.setenv(
        "ALLOWED_DATA_DIRECTORIES",
        '["temporary/notes", "temporary/projects"]',
    )
    monkeypatch.setenv("MARKDOWN_MAX_FILE_SIZE_BYTES", "1024")
    monkeypatch.setenv("MARKDOWN_MAX_TOTAL_SIZE_BYTES", "4096")

    # get_settings 使用了缓存；读取新环境变量前必须清除旧配置对象。
    get_settings.cache_clear()

    try:
        settings = get_settings()
    finally:
        # 测试结束再次清理，避免这个测试配置影响其他测试。
        get_settings.cache_clear()

    assert settings.app_name == "Test Interview Agent"
    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.database_path == Path("temporary/test.db")
    assert settings.markdown_source_directory == Path("temporary/notes")
    assert settings.allowed_data_directories == (
        Path("temporary/notes"),
        Path("temporary/projects"),
    )
    assert settings.markdown_max_file_size_bytes == 1024
    assert settings.markdown_max_total_size_bytes == 4096


def test_rejects_empty_allowed_data_directories() -> None:
    # 空白名单不能退化成“读取任意位置”。
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(allowed_data_directories=(), _env_file=None)


@pytest.mark.parametrize(
    "field_name",
    ["markdown_max_file_size_bytes", "markdown_max_total_size_bytes"],
)
def test_rejects_non_positive_markdown_byte_limits(field_name: str) -> None:
    # 两个大小字段共用同一条正整数约束。
    with pytest.raises(ValidationError, match="must be greater than zero"):
        Settings(**{field_name: 0}, _env_file=None)
