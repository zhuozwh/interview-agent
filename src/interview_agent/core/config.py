"""Central application configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from defaults, an optional .env file, and environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Interview Agent"
    app_env: str = "local"
    log_level: str = "INFO"
    database_path: Path = Path("data/interview_agent.db")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        """Normalize and validate standard logging levels."""
        normalized = value.upper()
        allowed_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed_levels:
            expected = ", ".join(sorted(allowed_levels))
            raise ValueError(f"LOG_LEVEL must be one of: {expected}")
        return normalized


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
