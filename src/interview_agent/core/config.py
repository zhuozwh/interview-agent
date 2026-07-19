"""集中定义和加载应用配置。"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """按默认值、.env、环境变量的优先关系加载配置。"""

    # pydantic-settings 负责读取 .env 和环境变量，并转换为下方声明的类型。
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Windows 环境变量通常使用大写；关闭大小写敏感后 APP_NAME 可映射到 app_name。
        case_sensitive=False,
        # .env 中暂时未被当前版本使用的字段不会导致应用启动失败。
        extra="ignore",
    )

    # 这些值既是字段的类型声明，也是没有外部配置时使用的本地默认值。
    app_name: str = "Interview Agent"
    app_env: str = "local"
    log_level: str = "INFO"
    database_path: Path = Path("data/interview_agent.db")

    # 赋值完成后统一把日志级别转换为大写，并拒绝 logging 不支持的值。
    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        """规范化并校验 Python 标准日志级别。"""
        normalized = value.upper()
        allowed_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed_levels:
            expected = ", ".join(sorted(allowed_levels))
            raise ValueError(f"LOG_LEVEL must be one of: {expected}")
        return normalized


# 缓存配置对象，保证同一进程通常只解析一次环境配置。
@lru_cache
def get_settings() -> Settings:
    """返回当前进程共用的配置对象。"""
    return Settings()
