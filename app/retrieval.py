"""三路检索器：BM25、Dense 与 RRF 融合。

设计上刻意保持“每种策略都能单独评测”：

- :class:`DenseRetriever` 负责语义召回，分数是余弦相似度，取值约在 [-1, 1]；
- :class:`BM25Retriever` 负责词面召回，分数无上界，对专有名词和版本号更敏感；
- :class:`HybridRetriever` 只融合**排名**，不比较两路原始分数。

融合用 Reciprocal Rank Fusion（RRF）：``score = Σ w / (k + rank)``。选它的原因是
两路分数不同量纲，直接加权求和需要额外调参且不可解释；RRF 只用排名，``k`` 取常用的
60，本项目未针对语料调整。代价是丢弃了“分数差距”信息——领先很多与险胜一名的
候选在 RRF 里几乎等价。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from app.domain import Chunk, SearchHit
from app.embeddings import EmbeddingModel
from app.tokenization import tokenize
from app.vector_cache import validate_vectors


def matches_filters(chunk: Chunk, filters: dict[str, str] | None) -> bool:
    """元数据等值过滤，当前支持 product / version。

    两个字段必须同时匹配，语义是“在指定产品的指定版本内检索”。空过滤器表示
    全库检索；这与“传了空字符串”不同，调用方需先用 ``RAGService.answer`` 里
    的 ``scope`` 清洗掉空值。
    """
    return not filters or all(chunk.metadata.get(key) == value for key, value in filters.items())


class DenseRetriever:
    """暴力余弦检索：把查询向量与全部文档向量逐一比较。

    这里不使用 FAISS/HNSW：当前语料为千级分块，暴力计算的结果完全确定、可逐条复现，
    也少一层依赖；代价是查询延迟随分块数线性增长。规模到十万级时应替换本类，
    而不是在调用方打补丁。
    """

    def __init__(self, embedding_model: EmbeddingModel) -> None:
        self.embedding_model = embedding_model
        self.chunks: list[Chunk] = []
        self.vectors: list[list[float]] = []

    def index(self, chunks: list[Chunk]) -> None:
        """整批重建索引；向量数量与分块数量必须一一对应。

        ``validate_vectors`` 会拦截空向量、维度不一致与非有限值，避免坏向量
        进入缓存后在后续查询中静默产生 NaN 相似度。
        """
        self.chunks = list(chunks)
        self.vectors = self.embedding_model.embed_documents([chunk.text for chunk in chunks])
        validate_vectors(self.vectors, len(chunks))

    def search(
        self, query: str, top_k: int, *, filters: dict[str, str] | None = None
    ) -> list[SearchHit]:
        if not self.chunks:
            return []
        query_vector = self.embedding_model.embed_query(query)
        scored = [
            (self._cosine(query_vector, vector), chunk)
            for chunk, vector in zip(self.chunks, self.vectors, strict=True)
            if matches_filters(chunk, filters)
        ]
        # 稳定排序的隐含前提：Python 的 sort 是稳定的，同分候选保持索引顺序。
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            SearchHit(chunk=chunk, score=score, dense_rank=rank)
            for rank, (score, chunk) in enumerate(scored[:top_k], start=1)
        ]

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        """余弦相似度；范数为 0 时按 1.0 处理以规避除零。

        本地 E5 与 Hashing 都已 L2 归一化，理论上可直接点积；这里仍显式除以范数，
        是为了在第三方接口返回未归一化向量时保持正确性。
        """
        if len(left) != len(right):
            raise ValueError("查询向量与文档向量维度不一致")
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(value * value for value in left)) or 1.0
        right_norm = math.sqrt(sum(value * value for value in right)) or 1.0
        return dot / (left_norm * right_norm)


class BM25Retriever:
    """Okapi BM25 词面检索，默认 k1=1.5、b=0.75。

    参数含义：``k1`` 控制词频饱和速度（越大越奖励重复出现），``b`` 控制文档长度
    归一化强度（0 关闭，1 完全归一化）。默认值是通用起点，本项目未针对语料调参；
    如果要改，必须在开发集上重新评测，不能凭感觉调整后沿用旧报告结论。

    索引是内存中的倒排统计（每块的词频计数器 + 全库文档频率），全量重建成本为 O(总词数)。
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.chunks: list[Chunk] = []
        self.term_frequencies: list[Counter[str]] = []
        self.document_frequencies: Counter[str] = Counter()
        self.average_length = 0.0

    def index(self, chunks: list[Chunk]) -> None:
        """计算词频、文档频率与平均文档长度。

        文档频率按“出现过该词的文档数”计数，因此对每块先用 ``set(tokens)`` 去重；
        若直接 update 全部 token，重复词会把 df 抬高、idf 被低估。
        """
        self.chunks = list(chunks)
        tokenized = [tokenize(chunk.text) for chunk in chunks]
        self.term_frequencies = [Counter(tokens) for tokens in tokenized]
        self.document_frequencies = Counter()
        for tokens in tokenized:
            self.document_frequencies.update(set(tokens))
        self.average_length = (
            sum(len(tokens) for tokens in tokenized) / len(tokenized) if tokenized else 0.0
        )

    def search(
        self, query: str, top_k: int, *, filters: dict[str, str] | None = None
    ) -> list[SearchHit]:
        query_terms = tokenize(query)
        if not self.chunks or not query_terms:
            return []
        scored: list[tuple[float, Chunk]] = []
        total = len(self.chunks)
        for chunk, frequencies in zip(self.chunks, self.term_frequencies, strict=True):
            if not matches_filters(chunk, filters):
                continue
            length = sum(frequencies.values())
            score = 0.0
            for term in query_terms:
                frequency = frequencies[term]
                if not frequency:
                    continue
                # IDF 采用 BM25 常用的 +0.5 平滑形式，避免高频词 idf 为负。
                df = self.document_frequencies[term]
                idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / (self.average_length or 1.0)
                )
                score += idf * frequency * (self.k1 + 1) / denominator
            scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        # 零分表示没有任何词面重合，不应作为“命中”返回；先切片再过滤会让
        # top_k 数量不足，因此这里在列表推导中过滤，允许结果少于 top_k。
        return [
            SearchHit(chunk=chunk, score=score, sparse_rank=rank)
            for rank, (score, chunk) in enumerate(scored[:top_k], start=1)
            if score > 0
        ]


class HybridRetriever:
    """使用 RRF 融合稠密与 BM25 排名，避免直接比较两种不可比的分数。

    融合细节：

    - 两路各取 ``max(top_k, 50)`` 个候选，保证小 ``top_k`` 时也有足够材料；
    - 同一分块在两路都出现时累加两份 RRF 贡献，并保留两路排名；
    - 输出的 ``score`` 是 RRF 分而非相似度，只用于排序与门控比较，不能当概率解释。
    """

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
        # 索引修订号由 RAGService 在发布快照时写入，用于把回答绑定到确定的索引版本。
        self.revision = ""

    def index(self, chunks: list[Chunk]) -> None:
        """两路索引必须基于同一份分块列表，否则融合时 ID 无法对齐。"""
        self.dense.index(chunks)
        self.sparse.index(chunks)

    def search(
        self, query: str, top_k: int, *, filters: dict[str, str] | None = None
    ) -> list[SearchHit]:
        candidate_k = max(top_k, 50)
        dense_hits = self.dense.search(query, candidate_k, filters=filters)
        sparse_hits = self.sparse.search(query, candidate_k, filters=filters)
        scores: defaultdict[str, float] = defaultdict(float)
        hits: dict[str, SearchHit] = {}

        for hit in dense_hits:
            scores[hit.chunk.id] += self.dense_weight / (self.rrf_k + (hit.dense_rank or 0))
            hits[hit.chunk.id] = hit
        for hit in sparse_hits:
            scores[hit.chunk.id] += self.sparse_weight / (self.rrf_k + (hit.sparse_rank or 0))
            # 同一分块可能已被稠密路收录，此处只补排名字段，不覆盖已有的 dense_rank。
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
