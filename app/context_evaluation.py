"""Paired context assembly at fixed retrieval inputs and fixed character budget caps."""

from collections import Counter
import hashlib
import json
from statistics import mean

from app.context import CONTEXT_POLICIES, context_usage, expand_context
from app.domain import Chunk, SearchHit
from app.evaluation import EvaluationCase, evidence_covered
from app.retrieval import matches_filters


def _refs(hits: list[SearchHit]) -> list[dict]:
    return [
        {"chunk_id": hit.chunk.id, "score": hit.score,
         "dense_rank": hit.dense_rank, "sparse_rank": hit.sparse_rank}
        for hit in hits
    ]


def compare_context_policies(
    cases: list[EvaluationCase], chunks: list[Chunk], searchers: dict,
    *, top_k: int, budgets: list[int],
) -> dict:
    if not cases or not searchers or any(case.split != "development" for case in cases):
        raise ValueError("配对上下文实验只接受非空开发集和检索策略")
    if not budgets or any(type(b) is not int or b < 1 for b in budgets) or not 1 <= top_k <= 50:
        raise ValueError("预算必须是正整数，top_k 范围 1..50")
    budgets = sorted(set(budgets))
    selected = [case for case in cases if case.should_answer and case.evidence]
    if not selected:
        raise ValueError("没有可回答且带证据范围的开发题")
    chunk_index = {chunk.id: chunk.to_dict() for chunk in chunks}
    results = {}
    for name, search in searchers.items():
        by_budget = {str(b): {"details": []} for b in budgets}
        for case in selected:
            # One search feeds every budget/policy; annotations are used only below for scoring.
            hits = search(case.question, top_k, filters=case.filters)
            if len(hits) > top_k or len({hit.chunk.id for hit in hits}) != len(hits):
                raise ValueError("检索器返回重复或超量候选")
            eligible = [chunk for chunk in chunks if matches_filters(chunk, case.filters)]
            eligible_ids = {chunk.id for chunk in eligible}
            if any(hit.chunk.id not in eligible_ids for hit in hits):
                raise ValueError("检索结果不符合版本或索引范围")
            fingerprint = hashlib.sha256(json.dumps(
                [hit.to_dict() for hit in hits], ensure_ascii=False, sort_keys=True
            ).encode()).hexdigest()
            for budget in budgets:
                row = {"id": case.case_id, "retrieval": _refs(hits),
                       "retrieval_sha256": fingerprint, "policies": {}}
                for policy in CONTEXT_POLICIES:
                    context = expand_context(hits, eligible, char_budget=budget, policy=policy)
                    chunk_index.update({hit.chunk.id: hit.chunk.to_dict() for hit in context})
                    covered = [evidence_covered(anchor, context) for anchor in case.evidence]
                    row["policies"][policy] = {
                        **context_usage(context),
                        "context": _refs(context),
                        "evidence_covered": covered,
                        "evidence_recall": sum(covered) / len(covered),
                        "all_evidence_covered": all(covered),
                    }
                baseline, candidate = [row["policies"][p] for p in CONTEXT_POLICIES]
                delta = candidate["evidence_recall"] - baseline["evidence_recall"]
                row["transition"] = "gained" if delta > 0 else "lost" if delta < 0 else "unchanged"
                row["evidence_recall_delta"] = delta
                by_budget[str(budget)]["details"].append(row)
        for result in by_budget.values():
            rows = result["details"]
            result["summary"] = {
                "evaluated_case_count": len(rows),
                "policies": {
                    policy: {
                        "complete_case_count": sum(
                            row["policies"][policy]["all_evidence_covered"] for row in rows
                        ),
                        "mean_evidence_recall": mean(
                            row["policies"][policy]["evidence_recall"] for row in rows
                        ),
                        **{key + "_total": sum(row["policies"][policy][key] for row in rows)
                           for key in ("context_chars", "unique_chars", "duplicate_chars")},
                    } for policy in CONTEXT_POLICIES
                },
                "paired": {outcome + "_case_ids": [
                    row["id"] for row in rows if row["transition"] == outcome
                ] for outcome in ("gained", "lost", "unchanged")},
                "mean_evidence_recall_delta": mean(row["evidence_recall_delta"] for row in rows),
            }
        results[name] = by_budget
    return {
        "case_status_counts": dict(Counter(
            "skipped_unanswerable" if not case.should_answer
            else "skipped_no_evidence" if not case.evidence else "evaluated"
            for case in cases
        )),
        "cases": {case.case_id: {
            "question": case.question, "category": case.category, "filters": case.filters,
            "review_status": case.review_status, "split": case.split,
            "expected_status": case.expected_status, "evidence": list(case.evidence),
        } for case in cases},
        "results": results,
        "chunks": chunk_index,
    }
