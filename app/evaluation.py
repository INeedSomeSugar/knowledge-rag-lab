from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Callable

from app.domain import SearchHit


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    question: str
    relevant_sources: frozenset[str]


def evaluate_retrieval(
    cases: list[EvaluationCase],
    search: Callable[[str, int], list[SearchHit]],
    top_k: int = 5,
) -> dict[str, object]:
    if not cases:
        raise ValueError("评测集不能为空")
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    details: list[dict[str, object]] = []

    for case in cases:
        hits = search(case.question, top_k)
        retrieved = [hit.chunk.source for hit in hits]
        matched = case.relevant_sources.intersection(retrieved)
        recall = len(matched) / len(case.relevant_sources)
        first_rank = next(
            (rank for rank, source in enumerate(retrieved, start=1) if source in case.relevant_sources),
            None,
        )
        reciprocal_rank = 1.0 / first_rank if first_rank else 0.0
        recalls.append(recall)
        reciprocal_ranks.append(reciprocal_rank)
        details.append(
            {
                "question": case.question,
                "expected": sorted(case.relevant_sources),
                "retrieved": retrieved,
                "recall": round(recall, 4),
                "reciprocal_rank": round(reciprocal_rank, 4),
            }
        )

    return {
        "case_count": len(cases),
        f"recall@{top_k}": round(mean(recalls), 4),
        "mrr": round(mean(reciprocal_ranks), 4),
        "details": details,
    }
