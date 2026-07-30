"""验证 Embedding 批处理、错误映射和向量完整性校验。"""

from collections.abc import Sequence

import pytest

from interview_agent.retrieval import (
    EmbeddingConfigurationError,
    EmbeddingInputError,
    EmbeddingProviderError,
    EmbeddingResponseError,
    embed_query,
    embed_texts,
)


class RecordingEmbedding:
    """用确定性结果记录调用批次，不依赖网络、模型文件或 API Key。"""

    model_name = "test-recording-v1"
    dimension = 2

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(tuple(texts))
        return [[float(len(text)), 1.0] for text in texts]

    def embed_query(self, query: str) -> list[float]:
        self.calls.append((f"query:{query}",))
        return [float(len(query)), 1.0]


def test_embed_texts_batches_in_stable_order() -> None:
    provider = RecordingEmbedding()

    vectors = embed_texts(provider, ("a", "bb", "ccc", "dddd", "eeeee"), batch_size=2)

    assert provider.calls == [("a", "bb"), ("ccc", "dddd"), ("eeeee",)]
    assert vectors == (
        (1.0, 1.0),
        (2.0, 1.0),
        (3.0, 1.0),
        (4.0, 1.0),
        (5.0, 1.0),
    )


def test_empty_input_does_not_call_provider() -> None:
    provider = RecordingEmbedding()

    assert embed_texts(provider, ()) == ()
    assert provider.calls == []


@pytest.mark.parametrize("batch_size", [0, -1, True])
def test_rejects_invalid_batch_size(batch_size: int) -> None:
    with pytest.raises(EmbeddingConfigurationError, match="batch_size"):
        embed_texts(RecordingEmbedding(), ("正文",), batch_size=batch_size)


def test_rejects_blank_text_without_leaking_content() -> None:
    with pytest.raises(EmbeddingInputError, match="index 1") as caught:
        embed_texts(RecordingEmbedding(), ("正常正文", "   "))

    assert "正常正文" not in str(caught.value)


def test_rejects_single_string_instead_of_text_sequence() -> None:
    """单个 str 也是 Sequence，但不能被误解成逐字符批处理。"""
    with pytest.raises(EmbeddingInputError, match="single string"):
        embed_texts(RecordingEmbedding(), "正文")


def test_maps_unexpected_provider_exception() -> None:
    class FailingEmbedding(RecordingEmbedding):
        def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
            raise TimeoutError("third-party details")

    with pytest.raises(EmbeddingProviderError, match="batch starting") as caught:
        embed_texts(FailingEmbedding(), ("私人正文",))

    assert isinstance(caught.value.__cause__, TimeoutError)
    assert "私人正文" not in str(caught.value)
    assert "third-party details" not in str(caught.value)


def test_embed_query_uses_query_specific_provider_method() -> None:
    provider = RecordingEmbedding()

    vector = embed_query(provider, "问题")

    assert vector == (2.0, 1.0)
    assert provider.calls == [("query:问题",)]


def test_embed_query_maps_provider_error_without_leaking_query() -> None:
    class FailingQueryEmbedding(RecordingEmbedding):
        def embed_query(self, query: str) -> list[float]:
            raise TimeoutError("third-party query details")

    with pytest.raises(EmbeddingProviderError, match="for query") as caught:
        embed_query(FailingQueryEmbedding(), "私人问题")

    assert isinstance(caught.value.__cause__, TimeoutError)
    assert "私人问题" not in str(caught.value)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ([], "0 vectors for 1 texts"),
        ([[1.0]], "dimension 1, expected 2"),
        ([[float("nan"), 1.0]], "not finite"),
        ([[0.0, 0.0]], "must not be all zeros"),
        ([[True, 1.0]], "not numeric"),
    ],
)
def test_rejects_invalid_provider_response(response, message: str) -> None:
    class InvalidEmbedding(RecordingEmbedding):
        def embed_texts(self, texts: Sequence[str]):
            return response

    with pytest.raises(EmbeddingResponseError, match=message):
        embed_texts(InvalidEmbedding(), ("正文",))


@pytest.mark.parametrize(
    ("model_name", "dimension", "message"),
    [
        (" ", 2, "model_name"),
        ("test", 0, "dimension"),
        ("test", True, "dimension"),
    ],
)
def test_rejects_invalid_provider_identity(
    model_name: str,
    dimension: int,
    message: str,
) -> None:
    provider = RecordingEmbedding()
    provider.model_name = model_name
    provider.dimension = dimension

    with pytest.raises(EmbeddingConfigurationError, match=message):
        embed_texts(provider, ("正文",))
