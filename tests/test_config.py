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
    assert settings.markdown_chunk_max_characters == 500
    assert settings.vector_store_path == Path("vector_index")
    assert settings.vector_collection_name == "interview_agent_chunks"
    assert settings.embedding_batch_size == 64
    assert settings.embedding_model_name == "BAAI/bge-small-zh-v1.5"
    assert settings.embedding_cache_directory == Path("embedding_models")
    assert settings.embedding_local_files_only is False
    assert settings.search_notes_min_score == 0.58
    assert settings.search_notes_max_total_characters == 6000
    assert settings.rag_context_max_characters == 8000
    assert settings.llm_api_key is None
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "deepseek-v4-flash"
    assert settings.llm_timeout_seconds == 60.0
    assert settings.llm_max_retries == 2
    assert settings.llm_temperature == 0.2
    assert settings.llm_max_tokens == 1200


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
    monkeypatch.setenv("MARKDOWN_CHUNK_MAX_CHARACTERS", "256")
    monkeypatch.setenv("VECTOR_STORE_PATH", "temporary/vectors")
    monkeypatch.setenv("VECTOR_COLLECTION_NAME", "test_chunks")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "16")
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "test/zh-model")
    monkeypatch.setenv("EMBEDDING_CACHE_DIRECTORY", "temporary/models")
    monkeypatch.setenv("EMBEDDING_LOCAL_FILES_ONLY", "true")
    monkeypatch.setenv("SEARCH_NOTES_MIN_SCORE", "0.5")
    monkeypatch.setenv("SEARCH_NOTES_MAX_TOTAL_CHARACTERS", "3000")
    monkeypatch.setenv("RAG_CONTEXT_MAX_CHARACTERS", "5000")
    monkeypatch.setenv("LLM_API_KEY", "test-only-secret")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.1")
    monkeypatch.setenv("LLM_MAX_TOKENS", "600")

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
    assert settings.markdown_chunk_max_characters == 256
    assert settings.vector_store_path == Path("temporary/vectors")
    assert settings.vector_collection_name == "test_chunks"
    assert settings.embedding_batch_size == 16
    assert settings.embedding_model_name == "test/zh-model"
    assert settings.embedding_cache_directory == Path("temporary/models")
    assert settings.embedding_local_files_only is True
    assert settings.search_notes_min_score == 0.5
    assert settings.search_notes_max_total_characters == 3000
    assert settings.rag_context_max_characters == 5000
    assert settings.llm_api_key.get_secret_value() == "test-only-secret"
    assert "test-only-secret" not in repr(settings)
    assert settings.llm_base_url == "https://llm.example.com/v1"
    assert settings.llm_model == "test-model"
    assert settings.llm_timeout_seconds == 15.0
    assert settings.llm_max_retries == 1
    assert settings.llm_temperature == 0.1
    assert settings.llm_max_tokens == 600


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


def test_rejects_non_positive_markdown_chunk_limit() -> None:
    # 字符上限和读取字节上限分别校验，避免混淆两种单位。
    with pytest.raises(ValidationError, match="must be greater than zero"):
        Settings(markdown_chunk_max_characters=0, _env_file=None)

    with pytest.raises(ValidationError, match="must not exceed 500"):
        Settings(markdown_chunk_max_characters=501, _env_file=None)


def test_rejects_invalid_vector_settings() -> None:
    """向量集合名和 Embedding 批大小在启动配置阶段就应失败。"""
    with pytest.raises(ValidationError, match="must be non-empty"):
        Settings(vector_collection_name=" ", _env_file=None)

    with pytest.raises(ValidationError, match="EMBEDDING_BATCH_SIZE"):
        Settings(embedding_batch_size=0, _env_file=None)

    with pytest.raises(ValidationError, match="must be non-empty"):
        Settings(embedding_model_name=" ", _env_file=None)


def test_rejects_invalid_search_notes_settings() -> None:
    """拒绝超出余弦范围的阈值和非正数正文预算。"""
    with pytest.raises(ValidationError, match="between -1 and 1"):
        Settings(search_notes_min_score=1.1, _env_file=None)

    with pytest.raises(ValidationError, match="between 1 and 20000"):
        Settings(search_notes_max_total_characters=0, _env_file=None)

    with pytest.raises(ValidationError, match="between 1 and 20000"):
        Settings(search_notes_max_total_characters=20_001, _env_file=None)


def test_rejects_invalid_rag_context_budget() -> None:
    """上下文预算必须能放入最小包络，同时保持本地请求有明确上限。"""
    with pytest.raises(ValidationError, match="between 512 and 50000"):
        Settings(rag_context_max_characters=511, _env_file=None)

    with pytest.raises(ValidationError, match="between 512 and 50000"):
        Settings(rag_context_max_characters=50_001, _env_file=None)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("llm_base_url", " ", "safe non-empty"),
        ("llm_model", "bad\nmodel", "safe non-empty"),
        ("llm_timeout_seconds", 0, "between 1 and 600"),
        ("llm_timeout_seconds", float("inf"), "between 1 and 600"),
        ("llm_max_retries", 4, "between 0 and 3"),
        ("llm_temperature", 2.1, "between 0 and 2"),
        ("llm_max_tokens", 0, "between 1 and 32768"),
        ("llm_max_retries", True, "must not be boolean"),
        ("llm_temperature", False, "must not be boolean"),
    ],
)
def test_rejects_invalid_llm_settings(
    field_name: str,
    value,
    message: str,
) -> None:
    """远程地址、超时、重试和费用相关边界在配置加载阶段失败。"""
    with pytest.raises(ValidationError, match=message):
        Settings(**{field_name: value}, _env_file=None)


def test_blank_llm_api_key_is_treated_as_unconfigured() -> None:
    """示例配置中的空密钥不能被误认为有效凭据。"""
    settings = Settings(llm_api_key=" ", _env_file=None)
    assert settings.llm_api_key is None
