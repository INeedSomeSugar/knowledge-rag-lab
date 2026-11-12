"""向量化适配器：统一稠密检索的调用接口。

三个实现对应三种使用场景，能力差异必须显式说明，不能互相冒充：

- :class:`HashingEmbedding`：零下载、零费用的开发基线，本质是“带符号哈希词袋”，
  只反映词面重合，不具备语义泛化能力。
- :class:`OpenAICompatibleEmbedding`：任意 OpenAI 兼容端点（含 Ollama），需要真实
  网络与服务可用性，失败不会静默降级。
- ``LocalE5Embedding``（见 ``app.local_embeddings``）：固定提交、离线校验的本地模型，
  是报告中“真实语义检索”的依据。

接口只有三个成员：``dimension``、``embed_documents``、``embed_query``。查询与文档
走不同方法，是因为 E5 一类模型要求 ``query:`` / ``passage:`` 前缀，混用会显著掉点。
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

from app.tokenization import tokenize


class EmbeddingModel(Protocol):
    """向量模型的最小协议；实现方负责保证维度一致与数值有限。"""

    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedding:
    """无需下载模型的开发基线；语义检索应显式选择本地模型或真实 API。

    实现是 feature hashing：每个 token 用 blake2b 映射到固定维度的一个下标，
    再用哈希奇偶决定正负号，最后做 L2 归一化。正负号的作用是让哈希冲突的
    期望内积接近 0，而不是系统性累加。

    这只保证“共享词项越多、相似度越高”，不具备同义改写能力。默认配置用它，
    是为了让新机器 clone 后无需下载模型即可跑通链路，不代表检索质量达标。
    """

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        # 哈希词袋对查询与文档使用同一映射，无需区分前缀。
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
    """通过 OpenAI 兼容接口批量取向量，支持自定义 base_url（含本地 Ollama）。

    超时与重试被显式收紧（45s、1 次）：评测脚本需要“失败即记录失败”，
    而不是在 SDK 内部长时间重试后把延迟算进检索耗时。
    """

    def __init__(
        self, *, model: str, api_key: str, base_url: str = "", batch_size: int = 10
    ) -> None:
        if not api_key:
            raise ValueError("使用 openai_compatible Embedding 时必须配置 API Key")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("请先安装项目依赖：pip install -e .") from exc
        kwargs: dict[str, object] = {"api_key": api_key, "timeout": 45.0, "max_retries": 1}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model
        self.batch_size = batch_size
        self.dimension = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """分批请求并按 ``index`` 还原顺序。

        服务端不保证返回顺序与输入一致，因此这里先排序再校验索引连续性；
        索引缺失或重复会直接报错，避免把错位的向量写进缓存。
        """
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = self.client.embeddings.create(model=self.model, input=batch)
            ordered = sorted(response.data, key=lambda item: item.index)
            if [item.index for item in ordered] != list(range(len(batch))):
                raise ValueError("Embedding 返回索引与输入不一致")
            vectors.extend(item.embedding for item in ordered)
        self.dimension = len(vectors[0])
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
