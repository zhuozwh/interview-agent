"""使用 FastEmbed 在本机生成中文文档和查询向量。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any

from fastembed import TextEmbedding

from interview_agent.retrieval.embedding import EmbeddingInputError

DEFAULT_FASTEMBED_MODEL = "BAAI/bge-small-zh-v1.5"
MAX_FASTEMBED_INPUT_CHARACTERS = 500

# BGE 官方建议短问题检索长文档时只给查询增加指令，文档片段保持原文。
_BGE_ZH_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
_DEFAULT_QUERY_PREFIXES = {
    DEFAULT_FASTEMBED_MODEL: _BGE_ZH_QUERY_PREFIX,
}


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
        query_prefix: str | None = None,
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
        if query_prefix is not None and not isinstance(query_prefix, str):
            raise FastEmbedConfigurationError(
                "FastEmbed query_prefix must be a string or None"
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
        self.query_prefix = (
            _DEFAULT_QUERY_PREFIXES.get(normalized_model, "")
            if query_prefix is None
            else query_prefix
        )
        if "\0" in self.query_prefix:
            raise FastEmbedConfigurationError(
                "FastEmbed query_prefix must contain no NUL"
            )
        self._dimension = dimension
        self._model: TextEmbedding | None = None

        # 查询预处理和输入边界也属于检索配置；变化后触发明确重建，避免新旧行为混用。
        query_prefix_fingerprint = hashlib.sha256(
            self.query_prefix.encode("utf-8")
        ).hexdigest()[:12]
        self._model_identity = (
            f"fastembed/{version('fastembed')}/{self.configured_model_name}"
            f"/max-chars={MAX_FASTEMBED_INPUT_CHARACTERS}"
            f"/query-prefix={query_prefix_fingerprint}"
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
        for index, text in enumerate(texts):
            _require_safe_input_length(text, label=f"text at index {index}")
        vectors = self._get_model().passage_embed(
            texts,
            batch_size=len(texts),
        )
        return [_to_float_list(vector) for vector in vectors]

    def embed_query(self, query: str) -> list[float]:
        """使用 query 路径为检索问题生成一条向量。"""
        prepared_query = f"{self.query_prefix}{query}"
        _require_safe_input_length(prepared_query, label="query")
        vectors = self._get_model().query_embed(prepared_query)
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


def _require_safe_input_length(text: str, *, label: str) -> None:
    """在 FastEmbed 截断前拒绝超长输入，且不把私人正文写入异常。"""
    if len(text) > MAX_FASTEMBED_INPUT_CHARACTERS:
        raise EmbeddingInputError(
            f"FastEmbed {label} exceeds "
            f"{MAX_FASTEMBED_INPUT_CHARACTERS} characters"
        )
