"""验证配置默认值和环境变量覆盖行为。"""

from pathlib import Path

from interview_agent.core.config import Settings, get_settings


def test_default_settings_load() -> None:
    # 禁用 .env，确保这个测试只验证代码中声明的默认值。
    settings = Settings(_env_file=None)

    assert settings.app_name == "Interview Agent"
    assert settings.app_env == "local"
    assert settings.log_level == "INFO"
    assert settings.database_path == Path("data/interview_agent.db")


def test_environment_variables_override_settings(monkeypatch) -> None:
    # monkeypatch 创建的环境变量只在当前测试期间有效，结束后会自动恢复。
    monkeypatch.setenv("APP_NAME", "Test Interview Agent")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("DATABASE_PATH", "temporary/test.db")

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
