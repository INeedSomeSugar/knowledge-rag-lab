from __future__ import annotations

import math
from collections import Counter, defaultdict

from app.domain import Chunk, SearchHit
from app.embeddings import EmbeddingModel
from app.tokenization import tokenize


class DenseRetriever:
    def __init__(self, embedding_model: EmbeddingModel) -> None:
        self.embedding_model = embedding_model
        self.chunks: list[Chunk] = []
        self.vectors: list[list[float]] = []

    def index(self, chunks: list[Chunk]) -> None:
        self.chunks = list(chunks)
        self.vectors = self.embedding_model.embed_documents([chunk.text for chunk in chunks])

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        if not self.chunks:
            return []
        query_vector = self.embedding_model.embed_query(query)
        scored = [
            (self._cosine(query_vector, vector), chunk)
            for chunk, vector in zip(self.chunks, self.vectors, strict=True)
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            SearchHit(chunk=chunk, score=score, dense_rank=rank)
            for rank, (score, chunk) in enumerate(scored[:top_k], start=1)
        ]

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            raise ValueError("查询向量与文档向量维度不一致")
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(value * value for value in left)) or 1.0
        right_norm = math.sqrt(sum(value * value for value in right)) or 1.0
        return dot / (left_norm * right_norm)


class BM25Retriever:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.chunks: list[Chunk] = []
        self.term_frequencies: list[Counter[str]] = []
        self.document_frequencies: Counter[str] = Counter()
        self.average_length = 0.0

    def index(self, chunks: list[Chunk]) -> None:
        self.chunks = list(chunks)
        tokenized = [tokenize(chunk.text) for chunk in chunks]
        self.term_frequencies = [Counter(tokens) for tokens in tokenized]
        self.document_frequencies = Counter()
        for tokens in tokenized:
            self.document_frequencies.update(set(tokens))
        self.average_length = (
            sum(len(tokens) for tokens in tokenized) / len(tokenized) if tokenized else 0.0
        )

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        query_terms = tokenize(query)
        if not self.chunks or not query_terms:
            return []
        scored: list[tuple[float, Chunk]] = []
        total = len(self.chunks)
        for chunk, frequencies in zip(self.chunks, self.term_frequencies, strict=True):
            length = sum(frequencies.values())
            score = 0.0
            for term in query_terms:
                frequency = frequencies[term]
                if not frequency:
                    continue
                df = self.document_frequencies[term]
                idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / (self.average_length or 1.0)
                )
                score += idf * frequency * (self.k1 + 1) / denominator
            scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            SearchHit(chunk=chunk, score=score, sparse_rank=rank)
            for rank, (score, chunk) in enumerate(scored[:top_k], start=1)
            if score > 0
        ]


class HybridRetriever:
    """使用 RRF 融合稠密与 BM25 排名，避免直接比较两种不可比的分数。"""

    def __init__(
        self,
        dense: DenseRetriever,
        sparse: BM25Retriever,
        *,
        rrf_k: int = 60,
        dense_weight: float = 1.0,
        sparse_weight: float = 1.0,
    ) -> None:
        self.dense = dense
        self.sparse = sparse
        self.rrf_k = rrf_k
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight

    def index(self, chunks: list[Chunk]) -> None:
        self.dense.index(chunks)
        self.sparse.index(chunks)

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        candidate_k = max(top_k * 3, 10)
        dense_hits = self.dense.search(query, candidate_k)
        sparse_hits = self.sparse.search(query, candidate_k)
        scores: defaultdict[str, float] = defaultdict(float)
        hits: dict[str, SearchHit] = {}

        for hit in dense_hits:
            scores[hit.chunk.id] += self.dense_weight / (self.rrf_k + (hit.dense_rank or 0))
            hits[hit.chunk.id] = hit
        for hit in sparse_hits:
            scores[hit.chunk.id] += self.sparse_weight / (self.rrf_k + (hit.sparse_rank or 0))
            if hit.chunk.id in hits:
                hits[hit.chunk.id].sparse_rank = hit.sparse_rank
            else:
                hits[hit.chunk.id] = hit

        ranked_ids = sorted(scores, key=scores.get, reverse=True)[:top_k]
        return [
            SearchHit(
                chunk=hits[chunk_id].chunk,
                score=scores[chunk_id],
                dense_rank=hits[chunk_id].dense_rank,
                sparse_rank=hits[chunk_id].sparse_rank,
            )
            for chunk_id in ranked_ids
        ]
