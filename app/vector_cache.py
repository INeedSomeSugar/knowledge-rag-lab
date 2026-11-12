"""Content-addressed document embeddings. Query vectors are deliberately not persisted.

缓存键是“命名空间 + 文本 SHA256”，命名空间由工厂按 provider/模型/端点/修订号
组合而成。由此得到两条性质：

- 同一文本换模型不会命中旧向量（命名空间不同）；
- 同一文本重复导入不会重复编码（摘要相同），这是冷建库与热重建差异的来源。

查询向量不落盘：它随问题变化、命中率极低，写缓存只会放大磁盘与隐私暴露面。
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sqlite3

from app.embeddings import EmbeddingModel


def validate_vectors(vectors: list[list[float]], expected_count: int) -> None:
    """校验向量数量、维度一致性与有限性。

    这三项是后续所有计算的前提：数量不齐会让分块与向量错位，维度不一致会在
    点积时抛错，NaN/Inf 会污染排序并让“最相关”变成任意顺序。
    """
    if len(vectors) != expected_count:
        raise ValueError("Embedding 返回向量数与输入数不一致")
    dimensions = {len(vector) for vector in vectors}
    if vectors and (0 in dimensions or len(dimensions) != 1):
        raise ValueError("Embedding 返回了空向量或不一致的维度")
    if any(not math.isfinite(value) for vector in vectors for value in vector):
        raise ValueError("Embedding 返回了非有限值")


class CachedEmbedding:
    """带 SQLite 缓存的向量模型包装，对外仍是 ``EmbeddingModel``。

    ``last_stats`` 记录本次调用的命中/去重/实际编码数量，评测脚本用它解释
    “冷建库耗时”与“热重建耗时”的差异；它属于诊断信息，不参与检索逻辑。
    """

    def __init__(self, model: EmbeddingModel, path: Path, namespace: str) -> None:
        self.model = model
        self.path = path
        # 命名空间再哈希一次：配置串可能很长，定长键更利于索引与日志展示。
        self.namespace = hashlib.sha256(namespace.encode()).hexdigest()
        self.dimension = model.dimension
        self.last_stats = {"cache_hits": 0, "embedded_texts": 0}
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS vectors (namespace TEXT, digest TEXT, vector TEXT NOT NULL, PRIMARY KEY(namespace, digest))"
            )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """先批量查缓存，只对缺失文本调用真实模型，再把新向量写回。

        注意去重发生在“同一批内相同文本”这一层：``missing`` 用字典按摘要去重，
        因此重复段落只编码一次，但返回结果仍按输入顺序完整还原。
        """
        keys = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
        found: dict[str, list[float]] = {}
        with sqlite3.connect(self.path) as connection:
            for key in set(keys):
                row = connection.execute(
                    "SELECT vector FROM vectors WHERE namespace=? AND digest=?",
                    (self.namespace, key),
                ).fetchone()
                if row:
                    found[key] = json.loads(row[0])
        missing = {key: text for key, text in zip(keys, texts) if key not in found}
        persisted_hits = sum(key in found for key in keys)
        if missing:
            vectors = self.model.embed_documents(list(missing.values()))
            validate_vectors(vectors, len(missing))
            found.update(zip(missing, vectors))
            # 合并后再整体校验一次：缓存里的旧向量若与新模型维度不同，这里会暴露。
            validate_vectors(list(found.values()), len(found))
            with sqlite3.connect(self.path) as connection:
                connection.executemany(
                    "INSERT OR REPLACE INTO vectors VALUES (?, ?, ?)",
                    [(self.namespace, key, json.dumps(found[key])) for key in missing],
                )
        result = [found[key] for key in keys]
        validate_vectors(result, len(texts))
        if result:
            self.dimension = len(result[0])
        self.last_stats = {
            "cache_hits": persisted_hits,
            # 同批内重复文本：总数 - 缓存命中 - 实际编码数。
            "deduplicated_texts": len(texts) - persisted_hits - len(missing),
            "embedded_texts": len(missing),
        }
        return result

    def embed_query(self, text: str) -> list[float]:
        """查询向量不缓存：单次使用、命中率低，写入只会增加磁盘与隐私成本。"""
        vector = self.model.embed_query(text)
        validate_vectors([vector], 1)
        return vector
