"""Observable retrieval failure stages, using the same context policy as the service.

检索失败不是一个二元结论。这个模块把一次检索拆成六个阶段，并判断金标证据是在
哪一步丢掉的：

``indexed → eligible → pool → top_k → expanded → context``

每一级的差值对应一种可行动的结论：分块没覆盖（index_gap）、版本过滤排除
（filter_excluded）、在池内但排名靠后（below_top_k）、预算不够（budget_limited）、
或根本不在候选池（outside_pool）。诊断只读开发集，保留测试集不参与任何调试。
"""

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
    """Union of half-open Python character ranges; overlap never inflates coverage.

    返回覆盖比例、缺失区间与是否完整。区间按起点排序后合并，因此两个分块重叠
    不会让覆盖率超过 1；缺失区间可直接用于解释“差哪一段没检索到”。
    """
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
    """只保留定位与排名，不带正文；正文统一放在结果顶层的 ``chunks`` 里。

    这样逐题明细不会重复存储同一段原文，报告体积可控，也不会因为多次引用
    产生不一致的文本副本。
    """
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
    """逐题诊断单条证据在各阶段的覆盖情况，并给出丢失原因。

    关键实现选择：``expanded`` 阶段用“足够大的预算”重算一次上下文，作为
    “若不限预算会得到什么”的反事实；它只用于诊断，永远不会送给模型。
    有了这一步，``budget_limited`` 才能与 ``below_top_k`` 区分开。
    """
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
    # 防御性校验：检索器若返回过滤外或索引外的分块，后续归因会全部失真。
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
        # 归因顺序从上游到下游：先看索引能不能覆盖，再看预算，最后才是排序。
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
        # 首次“完整覆盖”的累计排名：解释为什么 Top-5 拿不到，但 Top-50 可以。
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
        # 合并块记录的是组成它的原始分块 ID；未合并块回退到自身 ID。
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
    """批量诊断并聚合每种策略的阶段归因计数。

    不可回答题与无证据题标记为跳过而不是失败：它们不参与证据覆盖统计，
    但保留在 ``cases`` 里，使“为什么没算”可查。
    """
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
    # 合并块正文统一提升到顶层 chunks 表，逐题明细只留引用。
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
