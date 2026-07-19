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

    # Markdown 源目录表示真正要扫描的文件夹；允许目录则是它不能越过的安全边界。
    # 使用元组而不是可变列表，避免应用运行期间意外改变读取白名单。
    markdown_source_directory: Path = Path("knowledge")
    allowed_data_directories: tuple[Path, ...] = (Path("knowledge"),)

    # 两级字节上限分别限制单个文件和一次批量加载，防止意外读取超大目录。
    markdown_max_file_size_bytes: int = 2 * 1024 * 1024
    markdown_max_total_size_bytes: int = 20 * 1024 * 1024

    # Phase 1B 暂按字符数控制片段长度，不与具体 Embedding 模型的 tokenizer 绑定。
    markdown_chunk_max_characters: int = 1200

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

    @field_validator("allowed_data_directories")
    @classmethod
    def require_allowed_data_directories(
        cls, value: tuple[Path, ...]
    ) -> tuple[Path, ...]:
        """拒绝没有任何允许目录的配置，避免加载器失去路径边界。"""
        # 空白名单不能理解成“允许所有目录”，必须直接判定为配置错误。
        if not value:
            raise ValueError("ALLOWED_DATA_DIRECTORIES must not be empty")
        return value

    @field_validator(
        "markdown_max_file_size_bytes", "markdown_max_total_size_bytes"
    )
    @classmethod
    def require_positive_byte_limit(cls, value: int) -> int:
        """读取上限必须是正整数。"""
        # 0 或负数会让大小限制失去明确语义，因此在配置加载阶段就拒绝。
        if value <= 0:
            raise ValueError("Markdown byte limits must be greater than zero")
        return value

    @field_validator("markdown_chunk_max_characters")
    @classmethod
    def require_positive_chunk_limit(cls, value: int) -> int:
        """Markdown 片段长度上限必须是正整数。"""
        if value <= 0:
            raise ValueError(
                "MARKDOWN_CHUNK_MAX_CHARACTERS must be greater than zero"
            )
        return value


# 缓存配置对象，保证同一进程通常只解析一次环境配置。
@lru_cache
def get_settings() -> Settings:
    """返回当前进程共用的配置对象。"""
    return Settings()
