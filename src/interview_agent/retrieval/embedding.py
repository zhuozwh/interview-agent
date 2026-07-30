"""定义与供应商无关的 Embedding 接口，并统一校验返回向量。"""

from __future__ import annotations

import math
from collections.abc import Sequence
from numbers import Real
from typing import Protocol


class EmbeddingError(RuntimeError):
    """所有 Embedding 生成错误的基类。"""


class EmbeddingConfigurationError(EmbeddingError, ValueError):
    """Embedding 模型标识、维度或批大小配置无效。"""


class EmbeddingInputError(EmbeddingError, ValueError):
    """待生成向量的文本输入无效。"""


class EmbeddingProviderError(EmbeddingError):
    """具体 Embedding 供应方调用失败。"""


class EmbeddingResponseError(EmbeddingError):
    """供应方返回的向量数量、维度或数值不符合约定。"""


class EmbeddingProvider(Protocol):
    """业务层依赖的最小 Embedding 能力，不暴露供应方专有参数。"""

    @property
    def model_name(self) -> str:
        """返回可稳定区分模型版本的名称。"""

    @property
    def dimension(self) -> int:
        """返回每条向量固定的维度。"""

    def embed_texts(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """按输入顺序为一批待索引文档生成向量。"""

    def embed_query(self, query: str) -> Sequence[float]:
        """为一次检索问题生成查询向量。"""


def validate_embedding_provider(provider: EmbeddingProvider) -> tuple[str, int]:
    """校验模型身份信息，并返回规范化后的模型名称和维度。"""
    model_name = provider.model_name.strip()
    if not model_name or "\0" in model_name:
        raise EmbeddingConfigurationError(
            "Embedding model_name must be non-empty and contain no NUL"
        )

    dimension = provider.dimension
    # bool 是 int 的子类，但 True/False 显然不能表达有效向量维度。
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
        raise EmbeddingConfigurationError(
            "Embedding dimension must be a positive integer"
        )
    return model_name, dimension


def embed_texts(
    provider: EmbeddingProvider,
    texts: Sequence[str],
    *,
    batch_size: int = 64,
) -> tuple[tuple[float, ...], ...]:
    """分批调用供应方，并返回数量、维度和数值都已验证的不可变向量。"""
    _, dimension = validate_embedding_provider(provider)
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise EmbeddingConfigurationError(
            "Embedding batch_size must be a positive integer"
        )

    if isinstance(texts, (str, bytes)):
        raise EmbeddingInputError(
            "texts must be a sequence of strings, not a single string"
        )
    normalized_texts = tuple(texts)
    for index, text in enumerate(normalized_texts):
        if not isinstance(text, str) or not text.strip():
            # 错误只报告位置，不把可能包含隐私的正文写入异常。
            raise EmbeddingInputError(
                f"Embedding text at index {index} must be a non-empty string"
            )

    if not normalized_texts:
        return ()

    vectors: list[tuple[float, ...]] = []
    for batch_start in range(0, len(normalized_texts), batch_size):
        batch = normalized_texts[batch_start : batch_start + batch_size]
        try:
            raw_batch = provider.embed_texts(batch)
        except EmbeddingError:
            raise
        except Exception as error:
            # 不直接暴露第三方错误正文，调用方仍可通过异常链定位根因。
            raise EmbeddingProviderError(
                "Embedding provider failed for batch starting at "
                f"index {batch_start}"
            ) from error

        try:
            returned_vectors = tuple(raw_batch)
        except TypeError as error:
            raise EmbeddingResponseError(
                "Embedding provider returned a non-iterable response"
            ) from error

        if len(returned_vectors) != len(batch):
            raise EmbeddingResponseError(
                "Embedding provider returned "
                f"{len(returned_vectors)} vectors for {len(batch)} texts"
            )

        for offset, raw_vector in enumerate(returned_vectors):
            vectors.append(
                _validate_vector(
                    raw_vector,
                    dimension=dimension,
                    text_index=batch_start + offset,
                )
            )

    return tuple(vectors)


def embed_query(
    provider: EmbeddingProvider,
    query: str,
) -> tuple[float, ...]:
    """生成并校验单个查询向量，同时避免在异常中泄露问题正文。"""
    _, dimension = validate_embedding_provider(provider)
    if not isinstance(query, str) or not query.strip():
        raise EmbeddingInputError("Embedding query must be a non-empty string")

    try:
        raw_vector = provider.embed_query(query)
    except EmbeddingError:
        raise
    except Exception as error:
        raise EmbeddingProviderError("Embedding provider failed for query") from error

    return _validate_vector(
        raw_vector,
        dimension=dimension,
        text_index=0,
    )


def _validate_vector(
    raw_vector: Sequence[float],
    *,
    dimension: int,
    text_index: int,
) -> tuple[float, ...]:
    """把第三方向量转换为 float，并拒绝错误维度、非有限值和零向量。"""
    try:
        values = tuple(raw_vector)
    except TypeError as error:
        raise EmbeddingResponseError(
            f"Embedding vector at index {text_index} is not iterable"
        ) from error

    if len(values) != dimension:
        raise EmbeddingResponseError(
            "Embedding vector at index "
            f"{text_index} has dimension {len(values)}, expected {dimension}"
        )

    vector: list[float] = []
    for coordinate_index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, Real):
            raise EmbeddingResponseError(
                "Embedding coordinate at text index "
                f"{text_index}, coordinate {coordinate_index} is not numeric"
            )
        normalized = float(value)
        if not math.isfinite(normalized):
            raise EmbeddingResponseError(
                "Embedding coordinate at text index "
                f"{text_index}, coordinate {coordinate_index} is not finite"
            )
        vector.append(normalized)

    # Chroma 使用余弦距离时无法从零向量得到有意义的方向。
    if not any(value != 0.0 for value in vector):
        raise EmbeddingResponseError(
            f"Embedding vector at index {text_index} must not be all zeros"
        )
    return tuple(vector)
