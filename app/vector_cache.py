"""Content-addressed document embeddings. Query vectors are deliberately not persisted."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sqlite3

from app.embeddings import EmbeddingModel


def validate_vectors(vectors: list[list[float]], expected_count: int) -> None:
    if len(vectors) != expected_count:
        raise ValueError("Embedding 返回向量数与输入数不一致")
    dimensions = {len(vector) for vector in vectors}
    if vectors and (0 in dimensions or len(dimensions) != 1):
        raise ValueError("Embedding 返回了空向量或不一致的维度")
    if any(not math.isfinite(value) for vector in vectors for value in vector):
        raise ValueError("Embedding 返回了非有限值")


class CachedEmbedding:
    def __init__(self, model: EmbeddingModel, path: Path, namespace: str) -> None:
        self.model = model
        self.path = path
        self.namespace = hashlib.sha256(namespace.encode()).hexdigest()
        self.dimension = model.dimension
        self.last_stats = {"cache_hits": 0, "embedded_texts": 0}
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS vectors (namespace TEXT, digest TEXT, vector TEXT NOT NULL, PRIMARY KEY(namespace, digest))"
            )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
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
            "deduplicated_texts": len(texts) - persisted_hits - len(missing),
            "embedded_texts": len(missing),
        }
        return result

    def embed_query(self, text: str) -> list[float]:
        vector = self.model.embed_query(text)
        validate_vectors([vector], 1)
        return vector
