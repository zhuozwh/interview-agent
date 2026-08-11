"""集中定义和加载应用配置。"""

from functools import lru_cache
import math
from pathlib import Path

from pydantic import SecretStr, field_validator
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
    markdown_source_directory: Path = Path("knowledge/interview")
    project_source_directory: Path = Path("knowledge/projects")
    resume_source_directory: Path = Path("knowledge/resume")
    allowed_data_directories: tuple[Path, ...] = (Path("knowledge"),)

    # 两级字节上限分别限制单个文件和一次批量加载，防止意外读取超大目录。
    markdown_max_file_size_bytes: int = 2 * 1024 * 1024
    markdown_max_total_size_bytes: int = 20 * 1024 * 1024

    # 500 字符为当前 512-token BGE 模型保留特殊 token 余量。
    markdown_chunk_max_characters: int = 500

    # Chroma 只保存本地向量索引；模型身份和维度由实际 Embedding 适配器提供。
    vector_store_path: Path = Path("vector_index")
    vector_collection_name: str = "interview_agent_chunks"
    embedding_batch_size: int = 64

    # Phase 1E 默认使用本地中文 ONNX 模型；首次运行可下载，之后可切到纯离线模式。
    embedding_model_name: str = "BAAI/bge-small-zh-v1.5"
    embedding_cache_directory: Path = Path("embedding_models")
    embedding_local_files_only: bool = False

    # Tool 级阈值和正文预算用于拒绝弱证据，并限制返回给后续 LLM 的上下文。
    search_notes_min_score: float = 0.58
    search_notes_max_total_characters: int = 6000
    project_context_min_score: float = 0.535
    project_context_max_total_characters: int = 6000
    resume_context_min_score: float = 0.56
    resume_context_max_total_characters: int = 3000

    # RAG 预算覆盖 JSON 包络、引用元数据和正文，独立于 Tool 的正文预算。
    rag_context_max_characters: int = 8000

    # LLM 密钥可缺省，让健康检查和离线测试无需远程凭据即可启动。
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    # Grounded RAG 需要短而完整的最终答案；DeepSeek V4 默认思考会与答案
    # 共享 max_tokens，因此应用默认显式关闭，其他供应方可选 provider_default。
    llm_thinking_mode: str = "disabled"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1200

    # 单 Agent 的调用次数由代码固定为一次；这里只配置检索数量和回答长度。
    agent_top_k: int = 5
    agent_max_answer_characters: int = 8000

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
        """片段必须为正数，且不能越过当前 FastEmbed 的安全输入边界。"""
        if value <= 0:
            raise ValueError(
                "MARKDOWN_CHUNK_MAX_CHARACTERS must be greater than zero"
            )
        if value > 500:
            raise ValueError(
                "MARKDOWN_CHUNK_MAX_CHARACTERS must not exceed 500"
            )
        return value

    @field_validator("vector_collection_name", "embedding_model_name")
    @classmethod
    def require_non_empty_vector_name(cls, value: str) -> str:
        """Chroma 集合名和 Embedding 模型名都必须明确。"""
        normalized = value.strip()
        if not normalized or "\0" in normalized:
            raise ValueError(
                "Vector collection and embedding model names must be non-empty "
                "and contain no NUL"
            )
        return normalized

    @field_validator("embedding_batch_size")
    @classmethod
    def require_positive_embedding_batch_size(cls, value: int) -> int:
        """Embedding 批大小必须是正整数，避免无限循环或无意义调用。"""
        if value <= 0:
            raise ValueError("EMBEDDING_BATCH_SIZE must be greater than zero")
        return value

    @field_validator(
        "search_notes_min_score",
        "project_context_min_score",
        "resume_context_min_score",
        "search_notes_max_total_characters",
        "project_context_max_total_characters",
        "resume_context_max_total_characters",
        mode="before",
    )
    @classmethod
    def reject_boolean_tool_numbers(cls, value):
        """bool 是 int 的子类，但不能表达相似度或字符预算。"""
        if isinstance(value, bool):
            raise ValueError("Numeric Tool settings must not be boolean")
        return value

    @field_validator(
        "search_notes_min_score",
        "project_context_min_score",
        "resume_context_min_score",
    )
    @classmethod
    def require_valid_search_score(cls, value: float) -> float:
        """余弦相似度阈值只接受理论范围内的有限值。"""
        if not -1.0 <= value <= 1.0:
            raise ValueError("Tool minimum scores must be between -1 and 1")
        return value

    @field_validator(
        "search_notes_max_total_characters",
        "project_context_max_total_characters",
        "resume_context_max_total_characters",
    )
    @classmethod
    def require_positive_search_budget(cls, value: int) -> int:
        """Tool 返回正文总预算必须为正数。"""
        if not 1 <= value <= 20_000:
            raise ValueError(
                "Tool content budgets must be between 1 and 20000"
            )
        return value

    @field_validator("rag_context_max_characters")
    @classmethod
    def require_valid_rag_context_budget(cls, value: int) -> int:
        """完整 RAG 上下文预算需容纳包络，同时禁止无界增长。"""
        if not 512 <= value <= 50_000:
            raise ValueError(
                "RAG_CONTEXT_MAX_CHARACTERS must be between 512 and 50000"
            )
        return value

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def normalize_optional_llm_api_key(cls, value):
        """空环境变量等同于未配置，真实值由 SecretStr 防止意外展示。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("llm_base_url", "llm_model")
    @classmethod
    def require_non_empty_llm_endpoint_and_model(cls, value: str) -> str:
        """URL 和模型名不得为空或包含控制字符。"""
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > 2_048
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("LLM base URL and model must be safe non-empty text")
        return normalized

    @field_validator("llm_thinking_mode")
    @classmethod
    def require_valid_llm_thinking_mode(cls, value: str) -> str:
        """思考模式只能显式开关或保留供应方默认，不能携带任意 JSON。"""
        if not isinstance(value, str):
            raise ValueError(
                "LLM_THINKING_MODE must be provider_default, enabled, or disabled"
            )
        normalized = value.strip().casefold()
        if normalized not in {"provider_default", "enabled", "disabled"}:
            raise ValueError(
                "LLM_THINKING_MODE must be provider_default, enabled, or disabled"
            )
        return normalized

    @field_validator(
        "llm_timeout_seconds",
        "llm_max_retries",
        "llm_temperature",
        "llm_max_tokens",
        "agent_top_k",
        "agent_max_answer_characters",
        mode="before",
    )
    @classmethod
    def reject_boolean_llm_numbers(cls, value):
        """Python 的 bool 属于 int 子类，但不能表示数值型生成配置。"""
        if isinstance(value, bool):
            raise ValueError("Numeric generation settings must not be boolean")
        return value

    @field_validator("llm_timeout_seconds")
    @classmethod
    def require_valid_llm_timeout(cls, value: float) -> float:
        """每次远程尝试必须有有限超时。"""
        if not math.isfinite(value) or not 1.0 <= value <= 600.0:
            raise ValueError("LLM_TIMEOUT_SECONDS must be between 1 and 600")
        return value

    @field_validator("llm_max_retries")
    @classmethod
    def require_valid_llm_retries(cls, value: int) -> int:
        """限制自动重试，避免放大费用和远端故障。"""
        if not 0 <= value <= 3:
            raise ValueError("LLM_MAX_RETRIES must be between 0 and 3")
        return value

    @field_validator("llm_temperature")
    @classmethod
    def require_valid_llm_temperature(cls, value: float) -> float:
        """温度遵循 Chat Completions 的有效范围。"""
        if not math.isfinite(value) or not 0.0 <= value <= 2.0:
            raise ValueError("LLM_TEMPERATURE must be between 0 and 2")
        return value

    @field_validator("llm_max_tokens")
    @classmethod
    def require_valid_llm_max_tokens(cls, value: int) -> int:
        """输出 token 上限必须明确且受控。"""
        if not 1 <= value <= 32_768:
            raise ValueError("LLM_MAX_TOKENS must be between 1 and 32768")
        return value

    @field_validator("agent_top_k")
    @classmethod
    def require_valid_agent_top_k(cls, value: int) -> int:
        """Agent 不能绕过 search_notes 的 Top-K 上限。"""
        if not 1 <= value <= 10:
            raise ValueError("AGENT_TOP_K must be between 1 and 10")
        return value

    @field_validator("agent_max_answer_characters")
    @classmethod
    def require_valid_agent_answer_budget(cls, value: int) -> int:
        """模型回答进入应用响应前还有独立字符边界。"""
        if not 1 <= value <= 20_000:
            raise ValueError(
                "AGENT_MAX_ANSWER_CHARACTERS must be between 1 and 20000"
            )
        return value


# 缓存配置对象，保证同一进程通常只解析一次环境配置。
@lru_cache
def get_settings() -> Settings:
    """返回当前进程共用的配置对象。"""
    return Settings()
