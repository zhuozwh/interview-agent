"""验证 FastEmbed 本地适配器的模型身份、延迟加载和查询/文档分流。"""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from interview_agent.retrieval import (
    FastEmbedConfigurationError,
    FastEmbedEmbeddingProvider,
    embed_query,
    embed_texts,
)
from interview_agent.retrieval import fastembed_provider as provider_module


@pytest.fixture
def temporary_directory() -> Iterator[Path]:
    """模型缓存测试使用自动清理目录，不接触正式缓存。"""
    with TemporaryDirectory(prefix="interview-agent-fastembed-test-") as directory:
        yield Path(directory)


class FakeTextEmbedding:
    """模拟 FastEmbed API，避免单元测试下载或加载真实 ONNX 模型。"""

    instances: list["FakeTextEmbedding"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.passage_calls: list[tuple[str, ...]] = []
        self.query_calls: list[str] = []
        self.__class__.instances.append(self)

    @classmethod
    def list_supported_models(cls):
        return [{"model": "test/zh-model", "dim": 3}]

    def passage_embed(self, texts, *, batch_size: int):
        self.passage_calls.append(tuple(texts))
        return [[float(len(text)), 1.0, 0.5] for text in texts]

    def query_embed(self, query: str):
        self.query_calls.append(query)
        return iter([[1.0, float(len(query)), 0.5]])


def test_provider_delays_model_load_and_separates_documents_from_query(
    temporary_directory: Path,
    monkeypatch,
) -> None:
    FakeTextEmbedding.instances.clear()
    monkeypatch.setattr(provider_module, "TextEmbedding", FakeTextEmbedding)
    provider = FastEmbedEmbeddingProvider(
        model_name="test/zh-model",
        cache_directory=temporary_directory / "models",
        local_files_only=True,
        threads=2,
    )

    assert provider.dimension == 3
    assert provider.model_name.endswith("/test/zh-model")
    assert FakeTextEmbedding.instances == []

    document_vectors = embed_texts(provider, ("文档", "另一篇"), batch_size=2)
    query_vector = embed_query(provider, "问题")

    assert document_vectors == ((2.0, 1.0, 0.5), (3.0, 1.0, 0.5))
    assert query_vector == (1.0, 2.0, 0.5)
    assert len(FakeTextEmbedding.instances) == 1
    model = FakeTextEmbedding.instances[0]
    assert model.passage_calls == [("文档", "另一篇")]
    assert model.query_calls == ["问题"]
    assert model.kwargs["providers"] == ["CPUExecutionProvider"]
    assert model.kwargs["local_files_only"] is True
    assert Path(model.kwargs["cache_dir"]).is_absolute()


def test_rejects_unsupported_model_and_invalid_runtime_settings(
    temporary_directory: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(provider_module, "TextEmbedding", FakeTextEmbedding)

    with pytest.raises(FastEmbedConfigurationError, match="not supported"):
        FastEmbedEmbeddingProvider(
            model_name="unknown/model",
            cache_directory=temporary_directory / "models",
        )

    with pytest.raises(FastEmbedConfigurationError, match="threads"):
        FastEmbedEmbeddingProvider(
            model_name="test/zh-model",
            cache_directory=temporary_directory / "models",
            threads=0,
        )

    cache_file = temporary_directory / "cache-file"
    cache_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(FastEmbedConfigurationError, match="directory"):
        FastEmbedEmbeddingProvider(
            model_name="test/zh-model",
            cache_directory=cache_file,
        )
