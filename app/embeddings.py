from __future__ import annotations

import hashlib
import math
from typing import Protocol

from app.tokenization import tokenize


class EmbeddingModel(Protocol):
    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedding:
    """无需下载模型的开发用向量器；正式演示应切换真实 Embedding API。"""

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self.dimension
            sign = 1.0 if value & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class OpenAICompatibleEmbedding:
    def __init__(
        self, *, model: str, api_key: str, base_url: str = "", batch_size: int = 10
    ) -> None:
        if not api_key:
            raise ValueError("使用 openai_compatible Embedding 时必须配置 API Key")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("请先安装项目依赖：pip install -e .") from exc
        kwargs: dict[str, str] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model
        self.batch_size = batch_size
        self.dimension = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = self.client.embeddings.create(model=self.model, input=batch)
            vectors.extend(item.embedding for item in response.data)
        self.dimension = len(vectors[0])
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
