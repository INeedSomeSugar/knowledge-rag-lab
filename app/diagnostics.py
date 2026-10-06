"""Observable retrieval failure stages, using the same context policy as the service."""

from collections import Counter
from collections.abc import Callable

from app.context import CONTEXT_POLICIES, context_usage, expand_context
from app.domain import Chunk, SearchHit
from app.evaluation import EvaluationCase
from app.retrieval import matches_filters


POOL_K = 50  # Keep Hybrid's two RRF input pools identical to production for K <= 50.
OUTCOMES = {
    "context_covered": "上下文完整覆盖",
    "index_gap": "索引未完整保留标注范围",
    "filter_excluded": "过滤后证据不完整",
    "budget_limited": "预算截断证据",
    "below_top_k": "证据在候选池内、Top-K 外",
    "outside_pool": "候选池未完整覆盖证据",
}


def span_coverage(anchor: dict, chunks: list[Chunk]) -> dict:
    """Union of half-open Python character ranges; overlap never inflates coverage."""
    start, end = anchor["start_char"], anchor["end_char"]
    intervals: list[list[int]] = []
    for left, right in sorted(
        (max(start, chunk.start_char), min(end, chunk.end_char))
        for chunk in chunks
        if chunk.source == anchor["source"]
        and chunk.start_char < end
        and chunk.end_char > start
    ):
        if intervals and left <= intervals[-1][1]:
            intervals[-1][1] = max(intervals[-1][1], right)
        else:
            intervals.append([left, right])
    missing = []
    cursor = start
    for left, right in intervals:
        if cursor < left:
            missing.append([cursor, left])
        cursor = right
    if cursor < end:
        missing.append([cursor, end])
    covered = sum(right - left for left, right in intervals)
    return {
        "covered_chars": covered,
        "total_chars": end - start,
        "ratio": covered / (end - start),
        "complete": not missing,
        "intervals": intervals,
        "missing_intervals": missing,
    }


def _references(hits: list[SearchHit]) -> list[dict]:
    return [
        {
            "chunk_id": hit.chunk.id,
            "score": hit.score,
            "dense_rank": hit.dense_rank,
            "sparse_rank": hit.sparse_rank,
        }
        for hit in hits
    ]


def diagnose_case(
    case: EvaluationCase,
    chunks: list[Chunk],
    search: Callable,
    *,
    top_k: int,
    char_budget: int,
    context_policy: str = "neighbors",
) -> dict:
    # The public entry point also validates this; keep individual-case calls safe.
    if case.split != "development":
        raise ValueError("诊断仅接受 development；保留测试集不参与调试")
    if not case.should_answer or not case.evidence:
        raise ValueError("逐题诊断需要可回答问题及证据范围")
    if not 1 <= top_k <= POOL_K or char_budget < 1 or context_policy not in CONTEXT_POLICIES:
        raise ValueError("top_k 必须为 1..50，上下文预算必须大于 0")
    eligible = [chunk for chunk in chunks if matches_filters(chunk, case.filters)]
    pool = search(case.question, POOL_K, filters=case.filters)
    eligible_ids = {chunk.id for chunk in eligible}
    if any(hit.chunk.id not in eligible_ids for hit in pool):
        raise ValueError("检索器返回索引外或不符合版本过滤的分块")
    if len({hit.chunk.id for hit in pool}) != len(pool) or len(pool) > POOL_K:
        raise ValueError("检索器候选池存在重复分块或超出约定大小")
    retrieved = pool[:top_k]
    # Unbounded expansion is a diagnostic counterfactual, never sent to a model.
    expanded = expand_context(
        retrieved, eligible, char_budget=max(1, sum(len(chunk.text) for chunk in eligible)),
        policy=context_policy,
    )
    context = expand_context(retrieved, eligible, char_budget=char_budget, policy=context_policy)
    stages = {
        "indexed": chunks,
        "eligible": eligible,
        "pool": [hit.chunk for hit in pool],
        "top_k": [hit.chunk for hit in retrieved],
        "expanded": [hit.chunk for hit in expanded],
        "context": [hit.chunk for hit in context],
    }
    anchors = []
    for anchor in case.evidence:
        coverage = {name: span_coverage(anchor, items) for name, items in stages.items()}
        full = {name: value["complete"] for name, value in coverage.items()}
        if full["context"]:
            outcome = "context_covered"
        elif not full["indexed"]:
            outcome = "index_gap"
        elif not full["eligible"]:
            outcome = "filter_excluded"
        elif full["expanded"]:
            outcome = "budget_limited"
        elif full["pool"]:
            outcome = "below_top_k"
        else:
            outcome = "outside_pool"
        overlapping = [
            chunk for chunk in chunks if span_coverage(anchor, [chunk])["covered_chars"]
        ]
        eligible_overlapping = [chunk for chunk in overlapping if chunk.id in eligible_ids]
        first_overlap = next(
            (
                rank for rank, hit in enumerate(pool, 1)
                if span_coverage(anchor, [hit.chunk])["covered_chars"]
            ),
            None,
        )
        first_full = next(
            (
                rank for rank in range(1, len(pool) + 1)
                if span_coverage(anchor, stages["pool"][:rank])["complete"]
            ),
            None,
        )
        anchors.append(
            {
                "outcome": outcome,
                "coverage": coverage,
                "overlapping_chunk_ids": [chunk.id for chunk in overlapping],
                "first_overlap_rank": first_overlap,
                "first_full_coverage_rank": first_full,
                "requires_multiple_chunks": full["eligible"] and not any(
                    span_coverage(anchor, [chunk])["complete"] for chunk in eligible_overlapping
                ),
                "recovered_by_neighbors": full["context"] and not full["top_k"],
                "coverage_lost_to_budget": (
                    coverage["expanded"]["covered_chars"] > coverage["context"]["covered_chars"]
                ),
            }
        )
    top_recall = sum(item["coverage"]["top_k"]["complete"] for item in anchors) / len(anchors)
    context_recall = sum(item["coverage"]["context"]["complete"] for item in anchors) / len(anchors)
    matched_sources = case.relevant_sources.intersection(hit.chunk.source for hit in retrieved)
    def part_ids(hits):
        return list(dict.fromkeys(
            chunk_id for hit in hits
            for chunk_id in hit.chunk.metadata.get("context_chunk_ids", [hit.chunk.id])
        ))
    context_ids = set(part_ids(context))
    return {
        "document_hit": bool(matched_sources),
        "document_recall": len(matched_sources) / len(case.relevant_sources),
        "top_k_evidence_recall": top_recall,
        "context_evidence_recall": context_recall,
        "document_hit_evidence_incomplete": bool(matched_sources) and top_recall < 1,
        **context_usage(context),
        "context_policy": context_policy,
        "assembled_chunks": {
            hit.chunk.id: hit.chunk.to_dict() for hit in [*expanded, *context]
            if hit.chunk.metadata.get("context_policy") == "merged_neighbors"
        },
        "anchors": anchors,
        "pool": _references(pool),
        "retrieved": _references(retrieved),
        "context": _references(context),
        "budget_dropped_chunk_ids": [
            chunk_id for chunk_id in part_ids(expanded) if chunk_id not in context_ids
        ],
    }


def diagnose_retrieval(
    cases: list[EvaluationCase],
    chunks: list[Chunk],
    searchers: dict[str, Callable],
    *,
    top_k: int = 5,
    char_budget: int = 16000,
    context_policy: str = "neighbors",
) -> dict:
    if not cases or not searchers:
        raise ValueError("诊断需要问题和检索策略")
    if any(case.split != "development" for case in cases):
        raise ValueError("诊断仅接受 development；保留测试集不参与调试")
    if not 1 <= top_k <= POOL_K or char_budget < 1 or context_policy not in CONTEXT_POLICIES:
        raise ValueError("top_k 必须为 1..50，上下文预算必须大于 0")
    details = []
    for case in cases:
        status = (
            "skipped_unanswerable" if not case.should_answer
            else "skipped_no_evidence" if not case.evidence
            else "diagnosed"
        )
        details.append(
            {
                "id": case.case_id,
                "question": case.question,
                "category": case.category,
                "filters": case.filters,
                "review_status": case.review_status,
                "split": case.split,
                "expected_status": case.expected_status,
                "relevant_sources": sorted(case.relevant_sources),
                "evidence": list(case.evidence),
                "status": status,
                "strategies": {
                    name: diagnose_case(
                        case, chunks, search, top_k=top_k, char_budget=char_budget,
                        context_policy=context_policy,
                    )
                    for name, search in searchers.items()
                } if status == "diagnosed" else {},
            }
        )
    chunk_index = {chunk.id: chunk.to_dict() for chunk in chunks}
    for item in details:
        for result in item["strategies"].values():
            chunk_index.update(result.pop("assembled_chunks"))
    summaries = {}
    for name in searchers:
        values = [item["strategies"][name] for item in details if item["status"] == "diagnosed"]
        summaries[name] = {
            "evaluated_case_count": len(values),
            "document_hit_case_count": sum(item["document_hit"] for item in values),
            "document_hit_evidence_incomplete_case_count": sum(
                item["document_hit_evidence_incomplete"] for item in values
            ),
            "top_k_complete_case_count": sum(
                item["top_k_evidence_recall"] == 1 for item in values
            ),
            "context_complete_case_count": sum(
                item["context_evidence_recall"] == 1 for item in values
            ),
            "anchor_count": sum(len(item["anchors"]) for item in values),
            "anchor_outcomes": dict(Counter(
                anchor["outcome"] for item in values for anchor in item["anchors"]
            )),
            "neighbor_recovered_anchor_count": sum(
                anchor["recovered_by_neighbors"] for item in values for anchor in item["anchors"]
            ),
        }
    return {
        "case_count": len(cases),
        "case_status_counts": dict(Counter(item["status"] for item in details)),
        "strategies": summaries,
        "cases": details,
        "chunks": chunk_index,
    }
