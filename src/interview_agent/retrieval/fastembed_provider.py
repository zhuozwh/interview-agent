"""使用 FastEmbed 在本机生成中文文档和查询向量。"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any

from fastembed import TextEmbedding

DEFAULT_FASTEMBED_MODEL = "BAAI/bge-small-zh-v1.5"


class FastEmbedConfigurationError(ValueError):
    """FastEmbed 模型名称、缓存目录或线程配置无效。"""


class FastEmbedEmbeddingProvider:
    """把 FastEmbed 适配为项目内部统一的 EmbeddingProvider。"""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_FASTEMBED_MODEL,
        cache_directory: str | Path = "embedding_models",
        local_files_only: bool = False,
        threads: int | None = None,
    ) -> None:
        normalized_model = model_name.strip()
        if not normalized_model or "\0" in normalized_model:
            raise FastEmbedConfigurationError(
                "FastEmbed model_name must be non-empty and contain no NUL"
            )
        if threads is not None and (
            isinstance(threads, bool)
            or not isinstance(threads, int)
            or threads <= 0
        ):
            raise FastEmbedConfigurationError(
                "FastEmbed threads must be a positive integer or None"
            )

        cache_path = Path(cache_directory).resolve()
        if cache_path.exists() and not cache_path.is_dir():
            raise FastEmbedConfigurationError(
                "FastEmbed cache_directory must be a directory"
            )
        cache_path.mkdir(parents=True, exist_ok=True)

        model_metadata = _find_supported_model(normalized_model)
        dimension = model_metadata.get("dim")
        if isinstance(dimension, bool) or not isinstance(dimension, int):
            raise FastEmbedConfigurationError(
                "FastEmbed model metadata does not contain a valid dimension"
            )

        self.configured_model_name = normalized_model
        self.cache_directory = cache_path
        self.local_files_only = local_files_only
        self.threads = threads
        self._dimension = dimension
        self._model: TextEmbedding | None = None

        # 将适配器版本纳入身份；升级推理库后会触发一次明确的向量重建。
        self._model_identity = (
            f"fastembed/{version('fastembed')}/{self.configured_model_name}"
        )

    @property
    def model_name(self) -> str:
        """返回用于索引配置指纹的完整模型身份。"""
        return self._model_identity

    @property
    def dimension(self) -> int:
        """返回 FastEmbed 模型声明的固定向量维度。"""
        return self._dimension

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """使用 passage 路径为待索引片段生成向量。"""
        if not texts:
            return []
        vectors = self._get_model().passage_embed(
            texts,
            batch_size=len(texts),
        )
        return [_to_float_list(vector) for vector in vectors]

    def embed_query(self, query: str) -> list[float]:
        """使用 query 路径为检索问题生成一条向量。"""
        vectors = self._get_model().query_embed(query)
        try:
            vector = next(iter(vectors))
        except StopIteration as error:
            raise RuntimeError("FastEmbed returned no query vector") from error
        return _to_float_list(vector)

    def _get_model(self) -> TextEmbedding:
        """延迟加载公开模型，普通配置读取和单元测试不会触发下载。"""
        if self._model is None:
            self._model = TextEmbedding(
                model_name=self.configured_model_name,
                cache_dir=str(self.cache_directory),
                threads=self.threads,
                providers=["CPUExecutionProvider"],
                local_files_only=self.local_files_only,
            )
        return self._model


def _find_supported_model(model_name: str) -> dict[str, Any]:
    """从 FastEmbed 内置目录查找模型，拒绝隐式加载未知远程代码。"""
    for model in TextEmbedding.list_supported_models():
        if model.get("model") == model_name:
            return model
    raise FastEmbedConfigurationError(
        f"FastEmbed model is not supported: {model_name}"
    )


def _to_float_list(vector: Any) -> list[float]:
    """兼容 NumPy 数组和普通序列，统一交给上层继续做严格数值校验。"""
    if hasattr(vector, "tolist"):
        values = vector.tolist()
    else:
        values = list(vector)
    return [float(value) for value in values]
