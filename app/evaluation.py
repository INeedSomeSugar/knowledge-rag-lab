from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Callable

from app.domain import SearchHit


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    question: str
    relevant_sources: frozenset[str]
    case_id: str = ""
    category: str = "unspecified"
    reference_answer: str = ""
    should_answer: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, object], *, line_number: int) -> "EvaluationCase":
        question = str(value.get("question", "")).strip()
        if not question:
            raise ValueError(f"评测集第 {line_number} 行缺少 question")

        raw_sources = value.get("relevant_sources", [])
        if not isinstance(raw_sources, list) or not all(
            isinstance(source, str) and source.strip() for source in raw_sources
        ):
            raise ValueError(f"评测集第 {line_number} 行的 relevant_sources 必须是字符串数组")

        should_answer = value.get("should_answer", True)
        if not isinstance(should_answer, bool):
            raise ValueError(f"评测集第 {line_number} 行的 should_answer 必须是布尔值")
        if should_answer and not raw_sources:
            raise ValueError(
                f"评测集第 {line_number} 行是可回答问题，必须提供 relevant_sources"
            )

        return cls(
            question=question,
            relevant_sources=frozenset(source.strip() for source in raw_sources),
            case_id=str(value.get("id", "")).strip() or f"case-{line_number:04d}",
            category=str(value.get("category", "unspecified")).strip() or "unspecified",
            reference_answer=str(value.get("reference_answer", "")).strip(),
            should_answer=should_answer,
        )


def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"评测集第 {line_number} 行不是合法 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"评测集第 {line_number} 行必须是 JSON 对象")
        case = EvaluationCase.from_dict(value, line_number=line_number)
        if case.case_id in seen_ids:
            raise ValueError(f"评测集存在重复 id：{case.case_id}")
        seen_ids.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError("评测集不能为空")
    return cases


def _ndcg_at_k(retrieved: list[str], relevant: frozenset[str], top_k: int) -> float:
    seen_relevant: set[str] = set()
    dcg = 0.0
    for rank, source in enumerate(retrieved[:top_k], start=1):
        if source in relevant and source not in seen_relevant:
            dcg += 1.0 / math.log2(rank + 1)
            seen_relevant.add(source)
    ideal_count = min(len(relevant), top_k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / idcg if idcg else 0.0


def evaluate_retrieval(
    cases: list[EvaluationCase],
    search: Callable[[str, int], list[SearchHit]],
    top_k: int = 5,
) -> dict[str, object]:
    if not cases:
        raise ValueError("评测集不能为空")
    if top_k < 1:
        raise ValueError("top_k 必须大于 0")

    answerable_cases = [
        case for case in cases if case.should_answer and case.relevant_sources
    ]
    if not answerable_cases:
        raise ValueError("评测集中没有带相关来源标注的可回答问题")

    hits_at_k: list[float] = []
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    details: list[dict[str, object]] = []

    for case in answerable_cases:
        hits = search(case.question, top_k)
        retrieved = [hit.chunk.source for hit in hits]
        matched = case.relevant_sources.intersection(retrieved)
        hit_at_k = 1.0 if matched else 0.0
        recall = len(matched) / len(case.relevant_sources)
        first_rank = next(
            (rank for rank, source in enumerate(retrieved, start=1) if source in case.relevant_sources),
            None,
        )
        reciprocal_rank = 1.0 / first_rank if first_rank else 0.0
        ndcg = _ndcg_at_k(retrieved, case.relevant_sources, top_k)
        hits_at_k.append(hit_at_k)
        recalls.append(recall)
        reciprocal_ranks.append(reciprocal_rank)
        ndcgs.append(ndcg)
        details.append(
            {
                "id": case.case_id,
                "category": case.category,
                "question": case.question,
                "expected": sorted(case.relevant_sources),
                "retrieved": retrieved,
                "hit": bool(hit_at_k),
                "recall": round(recall, 4),
                "reciprocal_rank": round(reciprocal_rank, 4),
                "ndcg": round(ndcg, 4),
            }
        )

    hit_rate = round(mean(hits_at_k), 4)
    mrr = round(mean(reciprocal_ranks), 4)
    return {
        "case_count": len(cases),
        "evaluated_case_count": len(answerable_cases),
        "skipped_unanswerable_case_count": len(cases) - len(answerable_cases),
        f"hit_rate@{top_k}": hit_rate,
        f"recall@{top_k}": round(mean(recalls), 4),
        f"mrr@{top_k}": mrr,
        "mrr": mrr,
        f"ndcg@{top_k}": round(mean(ndcgs), 4),
        "details": details,
    }


def evaluate_strategies(
    cases: list[EvaluationCase],
    searchers: dict[str, Callable[[str, int], list[SearchHit]]],
    top_ks: list[int],
) -> dict[str, dict[str, dict[str, object]]]:
    if not searchers:
        raise ValueError("至少需要一种检索策略")
    normalized_top_ks = sorted(set(top_ks))
    if not normalized_top_ks or normalized_top_ks[0] < 1:
        raise ValueError("top_ks 必须包含大于 0 的整数")

    results: dict[str, dict[str, dict[str, object]]] = {}
    for strategy, search in searchers.items():
        results[strategy] = {
            str(top_k): evaluate_retrieval(cases, search, top_k=top_k)
            for top_k in normalized_top_ks
        }
    return results
