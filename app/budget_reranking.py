"""Label-free gates over pre-rerank RRF signals; no models or evaluation imports.

门控要回答的问题是：这次检索“已经足够确定”吗？如果确定，就跳过昂贵的交叉编码器。
判断只能使用重排前的信号——两路排名与 RRF 分数，不能偷看金标、题号或答案，
否则就成了评测期作弊。这也是本模块不导入 ``app.evaluation`` 的原因。

两个特征：

- ``overlap_count``：稠密与 BM25 的 Top-5 交集大小。两路都排进前五，说明两种
  完全不同的召回方式达成一致，通常不需要再判。
- ``rrf_margin``：第 1 名相对第 5 名的分数差距占比，衡量头部是否“拉开”。
"""

from dataclasses import asdict, dataclass
import math
from time import perf_counter
from typing import Protocol

from app.domain import SearchHit


@dataclass(frozen=True)
class RetrievalSignals:
    """门控可用的全部信息；刻意只包含三个标量。"""

    overlap_count: int
    rrf_margin: float
    candidate_count: int


def retrieval_signals(candidates: list[SearchHit]) -> RetrievalSignals:
    """Top-5 intersection is recoverable from equal-weight RRF's Top-50.

    Both ranks <= 5 give at least 2/65, ahead of single-route candidates
    (at most 1/61) and all but at most eight other double-route candidates.
    This contract is specific to the existing equal-weight RRF k=60 pipeline.

    上面这段推导是 ``dense_rank <= 5 and sparse_rank <= 5`` 的依据：在等权 RRF
    k=60 下，双路前五的候选人一定落在融合后的前 50 名内，因此只看 Top-50 候选
    就能还原交集，不需要回查完整排名表。换权重或换 k 必须重新推导。
    """
    if len({h.chunk.id for h in candidates}) != len(candidates):
        raise ValueError("duplicate candidates")
    # 出现任何 BGE 分数都说明输入已经重排过，门控应基于“重排前”状态，直接拒绝。
    if any(h.rerank_score is not None or h.rerank_rank is not None for h in candidates):
        raise ValueError("gate requires pre-rerank candidates, never BGE scores")
    if any(not math.isfinite(h.score) or h.score < 0 for h in candidates):
        raise ValueError("invalid RRF score")
    for h in candidates:
        if any(
            r is not None and (type(r) is not int or r < 1) for r in (h.dense_rank, h.sparse_rank)
        ):
            raise ValueError("invalid branch rank")
        if h.dense_rank is None and h.sparse_rank is None:
            raise ValueError("branch ranks required")
    # 强约束：候选必须按 RRF 分数降序传入，信号才与排名一致。
    if any(a.score < b.score for a, b in zip(candidates, candidates[1:])):
        raise ValueError("gate requires RRF descending order")
    overlap = sum(
        h.dense_rank is not None
        and h.dense_rank <= 5
        and h.sparse_rank is not None
        and h.sparse_rank <= 5
        for h in candidates
    )
    if overlap > 5:
        raise ValueError("invalid Top-5 intersection")
    margin = 1.0
    if candidates and candidates[0].score:
        # 以第 1 名为分母做归一化，使不同查询之间的 margin 可比。
        margin = (candidates[0].score - candidates[min(4, len(candidates) - 1)].score) / candidates[
            0
        ].score
    return RetrievalSignals(overlap, margin, len(candidates))


@dataclass(frozen=True)
class GatePolicy:
    """一条阈值规则：``family`` 选特征，``threshold`` 定触发点。

    触发语义刻意统一为“越不确信越触发”：

    - ``overlap``：交集 < 阈值 → 触发重排；
    - ``margin``：差距 <= 阈值 → 触发重排。

    阈值取值受限（overlap 0..6、margin 0..1.01）是因为它们由开发集网格搜索产生，
    合法范围之外的值没有评测依据。
    """

    family: str
    threshold: float

    def __post_init__(self):
        if self.family not in {"overlap", "margin"} or not math.isfinite(self.threshold):
            raise ValueError("invalid gate policy")
        if self.family == "overlap" and (
            self.threshold not in range(7) or type(self.threshold) is bool
        ):
            raise ValueError("overlap threshold must be 0..6")
        if self.family == "margin" and not 0 <= self.threshold <= 1.01:
            raise ValueError("margin threshold must be 0..1.01")

    def triggers(self, signals: RetrievalSignals) -> bool:
        # 只有一个候选时无可比较对象，重排也不会改变顺序，直接跳过。
        if signals.candidate_count < 2:
            return False
        return (
            signals.overlap_count < self.threshold
            if self.family == "overlap"
            else signals.rrf_margin <= self.threshold
        )

    def decide(self, candidates: list[SearchHit]) -> dict:
        """返回可写入 trace 的决策记录；``gate_ms`` 用于证明门控本身几乎不耗时。"""
        started = perf_counter()
        signals = retrieval_signals(candidates)
        triggered = self.triggers(signals)
        return {
            "triggered": triggered,
            "signals": asdict(signals),
            "policy": asdict(self),
            "gate_ms": (perf_counter() - started) * 1000,
        }


class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[SearchHit]) -> tuple[list[SearchHit], dict]: ...


class BudgetAwareReranker:
    """Drop-in RAGService.reranker interface; skipping never calls the model.

    与 ``BGEReranker`` 接口一致，因此可以在不改服务代码的前提下替换。
    跳过时 ``model`` 字段为 ``None``，报告据此区分“没触发”与“触发了但返回空 trace”。
    """

    def __init__(self, reranker: Reranker, policy: GatePolicy):
        self.reranker, self.policy = reranker, policy

    def rerank(self, query: str, candidates: list[SearchHit]) -> tuple[list[SearchHit], dict]:
        started = perf_counter()
        decision = self.policy.decide(candidates)
        if decision["triggered"]:
            ranked, model_trace = self.reranker.rerank(query, candidates)
        else:
            ranked, model_trace = list(candidates), None
        return ranked, {
            "provider": "budget_aware_bge",
            **decision,
            "model": model_trace,
            "elapsed_ms": (perf_counter() - started) * 1000,
        }
