"""检索与行为评测：指标实现、数据校验与逐题明细。

两类指标必须分清，报告里也不能混写：

- **文档级**：hit / recall / MRR / nDCG，只看命中的 ``source`` 是否在标注集合里；
- **证据级**：``evidence_covered`` 要求标注区间被检索结果**完整覆盖**，同文件里
  命中了错误段落不算。后者更接近“能不能据此作答”，也是本项目主推的口径。

评测集加载阶段做严格校验（字段类型、证据区间、状态一致性、划分不串组），
原因是错误标注会让指标看起来变好或变坏，但无法从结果反推是数据问题。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Callable

from app.domain import Chunk, SearchHit


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """一道评测题及其全部标注。

    字段语义：

    - ``reference_answer``：人工参考答案，只有 ``human_verified`` 的题才有；
    - ``evidence``：原文锚点（source + 字符区间 + 逐字 quote），用于证据级指标；
    - ``expected_status``：期望行为，允许 ``answered`` / ``insufficient_evidence`` /
      ``needs_clarification``，用于行为匹配率；
    - ``split``：``development`` 可反复调试，``test`` 保留到最后，脚本层有硬保护；
    - ``group_id``：同源问题的折叠分组，阈值选择按组切分以避免泄漏。
    """

    question: str
    relevant_sources: frozenset[str]
    case_id: str = ""
    category: str = "unspecified"
    reference_answer: str = ""
    should_answer: bool = True
    filters: dict[str, str] = field(default_factory=dict)
    evidence: tuple[dict[str, object], ...] = ()
    expected_status: str = "answered"
    review_status: str = "unreviewed"
    split: str = "development"
    group_id: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, object], *, line_number: int) -> "EvaluationCase":
        """从一行 JSON 构造并校验；错误信息带行号，便于定位坏样本。

        这些校验不是形式主义：``should_answer`` 与 ``relevant_sources`` 矛盾、
        ``expected_status`` 与 ``should_answer`` 不一致、证据区间非法，都会让
        后续指标失去意义，且很难从聚合数字看出来。
        """
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
            raise ValueError(f"评测集第 {line_number} 行是可回答问题，必须提供 relevant_sources")
        if not should_answer and raw_sources:
            raise ValueError(f"评测集第 {line_number} 行不可回答问题的来源应为空")
        filters = value.get("filters", {})
        if not isinstance(filters, dict) or any(
            key not in {"product", "version"} or not isinstance(item, str) or not item
            for key, item in filters.items()
        ):
            raise ValueError(f"评测集第 {line_number} 行 filters 不合法")
        evidence = value.get("evidence", [])
        if not isinstance(evidence, list):
            raise ValueError("evidence 必须为数组")
        for anchor in evidence:
            if (
                not isinstance(anchor, dict)
                or anchor.get("source") not in raw_sources
                or not isinstance(anchor.get("quote"), str)
                or not anchor["quote"].strip()
            ):
                raise ValueError("证据来源或原文不合法")
            if (
                type(anchor.get("start_char")) is not int
                or type(anchor.get("end_char")) is not int
                or not 0 <= anchor["start_char"] < anchor["end_char"]
            ):
                raise ValueError("证据范围不合法")
        expected = value.get(
            "expected_status", "answered" if should_answer else "insufficient_evidence"
        )
        if (
            expected not in {"answered", "insufficient_evidence", "needs_clarification"}
            or (expected == "answered") != should_answer
        ):
            raise ValueError("expected_status 与 should_answer 不一致")
        if value.get("review_status", "unreviewed") not in {"unreviewed", "human_verified"}:
            raise ValueError("review_status 不合法")
        if value.get("review_status") == "human_verified":
            # 人工核验不能只有状态标记：必须留下核验人、时间与参考答案，便于追溯。
            if not all(
                isinstance(value.get(key), str) and value[key].strip()
                for key in ("reviewed_by", "reviewed_at", "reference_answer")
            ):
                raise ValueError("人工核验条目必须记录 reviewed_by、reviewed_at 和参考答案")
            if should_answer and not evidence:
                raise ValueError("人工核验可回答问题必须有原文证据范围")
        if value.get("split", "development") not in {"development", "test"}:
            raise ValueError("split 不合法")

        return cls(
            question=question,
            relevant_sources=frozenset(source.strip() for source in raw_sources),
            case_id=str(value.get("id", "")).strip() or f"case-{line_number:04d}",
            category=str(value.get("category", "unspecified")).strip() or "unspecified",
            reference_answer=str(value.get("reference_answer", "")).strip(),
            should_answer=should_answer,
            filters=filters,
            evidence=tuple(evidence),
            expected_status=expected,
            review_status=str(value.get("review_status", "unreviewed")),
            split=str(value.get("split", "development")),
            group_id=str(value.get("group_id", "")),
        )


def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    """逐行读取 JSONL；空行跳过，重复 ID 与跨划分同组都直接报错。

    “同组不能跨 development/test”是防泄漏约束：同一主题的多个问法若一半在开发集、
    一半在测试集，调参就会变相看到测试内容。
    """
    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
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
    groups: dict[str, str] = {}
    for case in cases:
        if case.group_id:
            if case.group_id in groups and groups[case.group_id] != case.split:
                raise ValueError(f"同组问题跨越开发与测试划分：{case.group_id}")
            groups[case.group_id] = case.split
    return cases


def evidence_covered(anchor: dict[str, object], hits: list[SearchHit]) -> bool:
    """Full union coverage of a gold span; wrong paragraphs in the same file do not count.

    实现是区间并集判定：把所有与金标区间相交的命中裁到金标范围内，按起点排序后
    推进游标；只有当游标无空隙地走到终点才算覆盖。允许由多个分块共同拼出证据，
    这正是 ``merged_neighbors`` 策略要解决的问题。
    """
    start, end = int(anchor["start_char"]), int(anchor["end_char"])
    intervals = sorted(
        (max(start, hit.chunk.start_char), min(end, hit.chunk.end_char))
        for hit in hits
        if hit.chunk.source == anchor["source"]
        and hit.chunk.end_char > start
        and hit.chunk.start_char < end
    )
    cursor = start
    for left, right in intervals:
        if left > cursor:
            return False
        cursor = max(cursor, right)
    return cursor >= end


def _ndcg_at_k(retrieved: list[str], relevant: frozenset[str], top_k: int) -> float:
    """按 source 去重的 nDCG@k；同一文件的多个分块不重复得分。"""
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
    """对单一检索策略计算文档级与证据级指标，并保留逐题明细。

    只统计“可回答且有来源标注”的题；不可回答题（澄清/拒答）不参与检索指标，
    但仍计入 ``case_count``，使跳过数量可见而不是被悄悄丢弃。
    """
    if not cases:
        raise ValueError("评测集不能为空")
    if top_k < 1:
        raise ValueError("top_k 必须大于 0")

    answerable_cases = [case for case in cases if case.should_answer and case.relevant_sources]
    if not answerable_cases:
        raise ValueError("评测集中没有带相关来源标注的可回答问题")

    hits_at_k: list[float] = []
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    details: list[dict[str, object]] = []
    evidence_recalls: list[float] = []
    evidence_hits: list[float] = []

    for case in answerable_cases:
        hits = (
            search(case.question, top_k, filters=case.filters)
            if case.filters
            else search(case.question, top_k)
        )
        retrieved = [hit.chunk.source for hit in hits]
        matched = case.relevant_sources.intersection(retrieved)
        hit_at_k = 1.0 if matched else 0.0
        recall = len(matched) / len(case.relevant_sources)
        first_rank = next(
            (
                rank
                for rank, source in enumerate(retrieved, start=1)
                if source in case.relevant_sources
            ),
            None,
        )
        reciprocal_rank = 1.0 / first_rank if first_rank else 0.0
        ndcg = _ndcg_at_k(retrieved, case.relevant_sources, top_k)
        hits_at_k.append(hit_at_k)
        recalls.append(recall)
        reciprocal_ranks.append(reciprocal_rank)
        ndcgs.append(ndcg)
        # 证据级指标只在标注了 evidence 的题上计算；没有标注的题不按 0 计。
        covered = sum(evidence_covered(anchor, hits) for anchor in case.evidence)
        if case.evidence:
            evidence_recalls.append(covered / len(case.evidence))
            evidence_hits.append(float(covered > 0))
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
                "evidence_recall": covered / len(case.evidence) if case.evidence else None,
                "retrieved_chunks": [
                    {
                        "chunk_id": hit.chunk.id,
                        "source": hit.chunk.source,
                        "start_char": hit.chunk.start_char,
                        "end_char": hit.chunk.end_char,
                    }
                    for hit in hits
                ],
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
        # 同时给带后缀与不带后缀的键，兼容不同版本的报告读取方。
        "mrr": mrr,
        f"ndcg@{top_k}": round(mean(ndcgs), 4),
        "details": details,
        "evidence_annotated_case_count": len(evidence_recalls),
        f"evidence_recall@{top_k}": round(mean(evidence_recalls), 4) if evidence_recalls else None,
        f"evidence_hit_rate@{top_k}": round(mean(evidence_hits), 4) if evidence_hits else None,
    }


def evaluate_strategies(
    cases: list[EvaluationCase],
    searchers: dict[str, Callable[[str, int], list[SearchHit]]],
    top_ks: list[int],
) -> dict[str, dict[str, dict[str, object]]]:
    """一次检索、多个 top_k 复用结果，避免重复计算向量与 BM25 打分。

    缓存键包含过滤条件：同一问题在不同版本下必须各自检索，否则会把跨版本结果
    当成同一次检索。取 ``max(top_ks)`` 作为实际检索深度，再按各 k 截断。
    """
    if not searchers:
        raise ValueError("至少需要一种检索策略")
    normalized_top_ks = sorted(set(top_ks))
    if not normalized_top_ks or normalized_top_ks[0] < 1:
        raise ValueError("top_ks 必须包含大于 0 的整数")

    results: dict[str, dict[str, dict[str, object]]] = {}
    for strategy, search in searchers.items():
        cache: dict[tuple[str, tuple], list[SearchHit]] = {}

        def cached_search(query: str, top_k: int, **kwargs: object) -> list[SearchHit]:
            filters = kwargs.get("filters") or {}
            key = (query, tuple(sorted(filters.items())))
            if key not in cache:
                cache[key] = search(query, max(normalized_top_ks), **kwargs)
            return cache[key][:top_k]

        results[strategy] = {
            str(top_k): evaluate_retrieval(cases, cached_search, top_k=top_k)
            for top_k in normalized_top_ks
        }
    return results


def evaluate_answer_behavior(
    cases: list[EvaluationCase], answer: Callable, top_k: int
) -> dict[str, object]:
    """按期望状态分组统计系统行为，并单独报告上下文证据覆盖。

    这里**不**计算答案正确率（``answer_correctness`` 恒为 ``None``）：状态正确
    不等于内容正确，内容正确性需要人工或独立 Judge 评分。报告里保留这个空字段，
    是为了避免下游误把 ``answer_rate`` 当成准确率。
    """
    details = []
    for case in cases:
        response = answer(case.question, top_k, filters=case.filters)
        # 把响应里的 context 字典还原成 SearchHit，以便复用区间覆盖判定。
        context_hits = [
            SearchHit(
                Chunk(
                    item["chunk_id"],
                    item["document_id"],
                    item["source"],
                    item["text"],
                    item["start_char"],
                    item["end_char"],
                    item.get("metadata", {}),
                ),
                float(item["score"]),
            )
            for item in response.get("context", [])
        ]
        details.append(
            {
                "id": case.case_id,
                "category": case.category,
                "expected_status": case.expected_status,
                "reference_answer": case.reference_answer,
                "review_status": case.review_status,
                "gold_evidence": list(case.evidence),
                "filters": case.filters,
                "response": response,
                "context_evidence_recall": (
                    sum(evidence_covered(anchor, context_hits) for anchor in case.evidence)
                    / len(case.evidence)
                )
                if case.evidence
                else None,
            }
        )
    answerable = [item for item in details if item["expected_status"] == "answered"]
    unanswerable = [item for item in details if item["expected_status"] == "insufficient_evidence"]
    clarification = [item for item in details if item["expected_status"] == "needs_clarification"]

    def rate(items: list[dict], status: str) -> float | None:
        # 空分组返回 None 而不是 0：没有样本时“全错”和“无数据”必须可区分。
        return (
            round(sum(item["response"]["status"] == status for item in items) / len(items), 4)
            if items
            else None
        )

    return {
        "case_count": len(details),
        "answerable_count": len(answerable),
        "unanswerable_count": len(unanswerable),
        "clarification_count": len(clarification),
        "answer_rate_on_answerable": rate(answerable, "answered"),
        "false_refusal_rate": rate(answerable, "insufficient_evidence"),
        "false_answer_rate_on_unanswerable": rate(unanswerable, "answered"),
        "correct_refusal_rate": rate(unanswerable, "insufficient_evidence"),
        "clarification_rate": rate(clarification, "needs_clarification"),
        "error_count": sum(item["response"]["status"] == "error" for item in details),
        "evidence_only_count": sum(
            item["response"]["status"] == "evidence_only" for item in details
        ),
        "status_counts_by_expected": {
            expected: dict(
                Counter(
                    item["response"]["status"]
                    for item in details
                    if item["expected_status"] == expected
                )
            )
            for expected in ("answered", "insufficient_evidence", "needs_clarification")
        },
        "answer_correctness": None,
        "context_evidence_recall": (
            round(
                mean(
                    item["context_evidence_recall"]
                    for item in details
                    if item["context_evidence_recall"] is not None
                ),
                4,
            )
            if any(item["context_evidence_recall"] is not None for item in details)
            else None
        ),
        "note": "回答状态不等于答案正确性；语义正确性需要人工核验，模型核验不作为金标准。",
        "details": details,
    }
