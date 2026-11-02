"""Offline fixtures for the bounded development experiment, never human judgments."""

from copy import deepcopy
import json

import pytest

from app.deepseek import DeepSeekGenerator
from app.generation import Completion
from scripts.compare_generation import (
    BoundedDeepSeek,
    BudgetStopped,
    control_draft,
    prepare_blind_review,
    reviewed_cases,
)
from scripts.review_answers import export_review, score_review


def response_fixture():
    text = "你可以定义在返回响应后运行的后台任务。"
    return {
        "question": "后台任务什么时候执行？",
        "status": "answered",
        "answer": text + " [1]",
        "claims": [{"text": text, "evidence": [{"citation": 1, "quote": text}]}],
        "context": [
            {
                "chunk_id": "fixture-c",
                "document_id": "fixture-d",
                "source": "fixture.md",
                "text": text,
                "start_char": 0,
                "end_char": len(text),
                "score": 1.0,
            }
        ],
    }


@pytest.mark.parametrize(
    "split,status", [("development", "unreviewed"), ("test", "human_verified")]
)
def test_rejects_unreviewed_or_held_out_input_before_provider(tmp_path, split, status):
    from pathlib import Path

    row = json.loads(
        Path("evaluation/questions.support.candidate.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    row.update(
        split=split,
        review_status=status,
        reviewed_by="offline-test-fixture",
        reviewed_at="2026-10-28T00:00:00+00:00",
    )
    path = tmp_path / "fixture.jsonl"
    path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="拒绝未审题及保留测试"):
        reviewed_cases(path)


def test_synthetic_contradiction_preserves_quotes_and_original():
    response = response_fixture()
    original = deepcopy(response)
    positive = control_draft(response, "support-007", False)
    negative = control_draft(response, "support-007", True)
    assert response == original
    assert positive.status == negative.status == "answered"
    assert positive.claims[0]["evidence"] == negative.claims[0]["evidence"]
    assert "先执行完成" in negative.claims[0]["text"]
    assert positive.answer != negative.answer


@pytest.mark.parametrize("limit,tokens", [("calls", 10), ("tokens", 11), ("unknown", None)])
def test_budget_stops_before_an_additional_provider_call(tmp_path, monkeypatch, limit, tokens):
    calls = []

    def complete(*args):
        calls.append(True)
        return Completion("{}", total_tokens=tokens, model="offline-fixture", finish_reason="stop")

    monkeypatch.setattr(DeepSeekGenerator, "_complete", complete)
    generator = object.__new__(BoundedDeepSeek)
    generator.protocol = {
        "budget": {
            "max_completion_calls": 1 if limit == "calls" else 10,
            "soft_observed_tokens": 10 if limit == "tokens" else 100,
        }
    }
    generator.journal = tmp_path / "calls.jsonl"
    generator.calls = generator.tokens = 0
    generator.unknown_usage = generator.stopped = False
    generator.job = "offline-fixture"
    generator._complete("system", "data")
    with pytest.raises(BudgetStopped):
        generator._complete("system", "data")
    assert len(calls) == generator.calls == 1
    assert generator.stopped
    entries = [json.loads(x) for x in generator.journal.read_text().splitlines()]
    assert len(entries) == 1 and entries[0]["status"] == "ok"


def test_new_blind_answers_stay_pending_and_hide_verifier_outcome(tmp_path):
    response = response_fixture()
    detail = {
        "id": "fixture-case",
        "response": response,
        "filters": {},
        "review_status": "human_verified",
        "reference_answer": "响应返回后执行",
        "gold_evidence": [],
    }
    checked = deepcopy(detail)
    checked["response"].update(status="insufficient_evidence", reason="support_verification_failed")
    report = {
        "answers": {"details": [checked]},
        "verification_comparison": {
            "design": "same_draft_same_context",
            "without_verification": {"details": [detail]},
        },
    }
    for arm in ("bm25", "e5_hybrid"):
        path = tmp_path / f"{arm}.report.json"
        path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        export_review(path, tmp_path / f"{arm}.answers.pending.jsonl")
        score = score_review(path, tmp_path / f"{arm}.answers.pending.jsonl")
        assert score["reviewed_count"] == 0
        assert score["draft_correctness_on_reviewed"]["rate"] is None
    prepare_blind_review(
        tmp_path, {"blind_review_seed": 7, "grading_priority_cases": ["fixture-case"]}
    )
    markdown = (tmp_path / "answers.priority.md").read_text(encoding="utf-8")
    assert "support_verification_failed" not in markdown
    assert "bm25" not in markdown and "e5_hybrid" not in markdown
    rows = [
        json.loads(x)
        for x in (tmp_path / "answers.blind.pending.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len({x["review_id"] for x in rows}) == len(rows) == 2
    assert all(x["review_status"] == "pending" and x["answer_correct"] is None for x in rows)
