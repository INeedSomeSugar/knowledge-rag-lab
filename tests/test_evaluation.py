import json
from pathlib import Path

import pytest

from app.domain import Chunk, SearchHit
from app.evaluation import (
    EvaluationCase,
    evaluate_retrieval,
    evaluate_strategies,
    load_evaluation_cases,
)
from tests.test_service import build_service


def test_retrieval_metrics(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    service.ingest("工单手册.md", "P0 事件需要五分钟内响应。")
    service.ingest("员工手册.md", "正式员工每周最多远程办公两天。")
    cases = [
        EvaluationCase("P0 多久响应", frozenset({"工单手册.md"})),
        EvaluationCase("远程办公几天", frozenset({"员工手册.md"})),
    ]

    result = evaluate_retrieval(cases, service.search, top_k=1)

    assert result["recall@1"] == 1.0
    assert result["mrr"] == 1.0
    assert result["hit_rate@1"] == 1.0
    assert result["ndcg@1"] == 1.0


def test_metrics_do_not_reward_duplicate_chunks_from_same_source() -> None:
    chunks = [
        Chunk(str(index), source, source, "text", 0, 4)
        for index, source in enumerate(["a.md", "a.md", "b.md"], start=1)
    ]
    hits = [SearchHit(chunk=chunk, score=1.0) for chunk in chunks]
    case = EvaluationCase("问题", frozenset({"a.md", "b.md"}), case_id="case-1")

    result = evaluate_retrieval([case], lambda _query, top_k: hits[:top_k], top_k=3)

    assert result["hit_rate@3"] == 1.0
    assert result["recall@3"] == 1.0
    assert result["mrr@3"] == 1.0
    assert result["ndcg@3"] == pytest.approx(0.9197, abs=0.0001)


def test_load_cases_and_skip_unanswerable_for_retrieval_metrics(tmp_path: Path) -> None:
    path = tmp_path / "questions.jsonl"
    rows = [
        {
            "id": "answerable-1",
            "category": "fact",
            "question": "可以回答吗？",
            "relevant_sources": ["guide.md"],
            "reference_answer": "可以。",
            "should_answer": True,
        },
        {
            "id": "unanswerable-1",
            "category": "out_of_scope",
            "question": "知识库里没有什么？",
            "relevant_sources": [],
            "reference_answer": "根据当前知识库无法确定。",
            "should_answer": False,
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )
    cases = load_evaluation_cases(path)
    chunk = Chunk("1", "doc-1", "guide.md", "text", 0, 4)

    results = evaluate_strategies(
        cases,
        {"bm25": lambda _query, _top_k: [SearchHit(chunk=chunk, score=1.0)]},
        [1, 3],
    )

    assert results["bm25"]["1"]["evaluated_case_count"] == 1
    assert results["bm25"]["1"]["skipped_unanswerable_case_count"] == 1


def test_loader_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    path = tmp_path / "questions.jsonl"
    row = {
        "id": "duplicate",
        "question": "问题",
        "relevant_sources": ["guide.md"],
    }
    path.write_text(
        f"{json.dumps(row, ensure_ascii=False)}\n{json.dumps(row, ensure_ascii=False)}",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="重复 id"):
        load_evaluation_cases(path)
