"""显式启用时，用脱敏问题验证真实 OpenAI-compatible LLM 链路。"""

import os

import pytest

from interview_agent.core.config import Settings
from interview_agent.llm import (
    ChatMessage,
    ChatRole,
    OpenAICompatibleLLMClient,
)

_RUN_REAL_LLM_ACCEPTANCE = os.getenv("RUN_REAL_LLM_ACCEPTANCE") == "1"


@pytest.mark.skipif(
    not _RUN_REAL_LLM_ACCEPTANCE,
    reason="set RUN_REAL_LLM_ACCEPTANCE=1 to call the configured remote LLM",
)
def test_real_llm_returns_bounded_synthetic_answer() -> None:
    """只发送公开技术问题，不发送 Vault、简历、路径或本机运行数据。"""
    settings = Settings()
    if settings.llm_api_key is None:
        pytest.fail("LLM_API_KEY is required when real LLM acceptance is enabled")

    with OpenAICompatibleLLMClient(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        temperature=0.0,
        max_tokens=128,
    ) as client:
        response = client.complete(
            (
                ChatMessage(
                    role=ChatRole.SYSTEM,
                    content="请用一句中文回答公开的 C++ 基础问题。",
                ),
                ChatMessage(
                    role=ChatRole.USER,
                    content="RAII 的核心作用是什么？",
                ),
            )
        )

    assert response.content.strip()
    assert response.usage.total_tokens > 0
    assert response.finish_reason in {"stop", "length"}
