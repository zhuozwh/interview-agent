from pathlib import Path

from interview_agent.core.config import Settings, get_settings


def test_default_settings_load() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "Interview Agent"
    assert settings.app_env == "local"
    assert settings.log_level == "INFO"
    assert settings.database_path == Path("data/interview_agent.db")


def test_environment_variables_override_settings(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "Test Interview Agent")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("DATABASE_PATH", "temporary/test.db")
    get_settings.cache_clear()

    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert settings.app_name == "Test Interview Agent"
    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.database_path == Path("temporary/test.db")
