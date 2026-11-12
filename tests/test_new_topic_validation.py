"""新主题验证协议：人工准入、冻结完整性与失败留痕。

核心约束是“助手检查不等于人工确认”：未确认时 ``admitted`` 必须为 false，且不得加载
模型或执行检索。冻结后任何输入、依赖、模型文件变化都要被发现；失败必须保留部分产物
和失败记录，不能覆盖重跑。
"""

from copy import deepcopy

import pytest

from app.domain import Chunk, SearchHit
from app.evaluation import EvaluationCase
from scripts import validate_new_topics as validation
from scripts.report_new_topics import classify, complete, coverage


def review_pair():
    question = {
        "id": "budget-independent-001",
        "question": "Question",
        "evidence": [{"quote": "abc"}],
    }
    record = {
        "id": question["id"],
        "candidate_sha256": validation.digest(question),
        "corpus_sha256": "corpus",
        "status": "human_verified",
        "reviewed_by": "Test human",
        "reviewed_at": "2020-01-01T00:00:00+08:00",
        "decision_source": "synthetic unit-test fixture",
        **dict.fromkeys(validation.CHECKS, True),
        "semantic_overlap_with_development": False,
    }
    return question, record


def test_pending_is_not_admission_even_with_assistant_content_checks():
    q, r = review_pair()
    r.update(status="pending", reviewed_by="Codex")
    assert validation.validate_human([q], [r], "corpus") == [q["id"]]


@pytest.mark.parametrize(
    "change",
    [
        {"reviewed_by": "Codex"},
        {"reviewed_by": "助手"},
        {"reviewed_by": ""},
        {"reviewed_at": "2020-01-01"},
        {"reviewed_at": "2999-01-01T00:00:00Z"},
        {"question_and_scope": 1},
        {"answer_and_behavior": None},
        {"evidence_sufficient_and_complete": False},
        {"semantic_overlap_with_development": None},
        {"semantic_overlap_with_development": True},
        {"decision_source": ""},
        {"candidate_sha256": "stale"},
        {"corpus_sha256": "other"},
    ],
)
def test_rejects_assistant_stale_ambiguous_or_incomplete_human_records(change):
    q, r = review_pair()
    r.update(change)
    with pytest.raises(ValueError):
        validation.validate_human([q], [r], "corpus")


def test_changed_evidence_requires_new_human_decision():
    q, r = review_pair()
    changed = deepcopy(q)
    changed["evidence"][0]["quote"] = "abcd"
    with pytest.raises(ValueError, match="stale"):
        validation.validate_human([changed], [r], "corpus")


def test_duplicate_or_missing_reviews_cannot_silently_reduce_denominator():
    q, r = review_pair()
    for records in ([], [r, r], [{**r, "id": "other"}]):
        with pytest.raises(ValueError):
            validation.validate_human([q], records, "corpus")


def test_execute_blocks_before_protocol_or_model_loading(monkeypatch, tmp_path):
    monkeypatch.setattr(validation, "check", lambda out: {"admitted": False})

    def forbidden(out):
        pytest.fail("protocol/model work happened before actual human admission")

    monkeypatch.setattr(validation, "verify_protocol", forbidden)
    with pytest.raises(ValueError, match="human confirmation"):
        validation.execute(tmp_path)
    assert not (tmp_path / "attempt-001").exists()


def test_failed_execution_retains_partial_trial_and_never_overwrites(monkeypatch, tmp_path):
    from scripts import report_new_topics

    monkeypatch.setattr(validation, "check", lambda out: {"admitted": True})
    monkeypatch.setattr(validation, "verify_protocol", lambda out: ({}, []))
    monkeypatch.setattr(validation, "verify_protected", lambda out: {"synthetic": True})
    (tmp_path / "protocol.json").write_text("synthetic")

    def failing(out, attempt, protocol, chunks):
        (attempt / "trials.jsonl").write_text("synthetic partial trial\n")
        raise RuntimeError("synthetic model failure")

    monkeypatch.setattr(report_new_topics, "run_models", failing)
    with pytest.raises(RuntimeError, match="synthetic"):
        validation.execute(tmp_path)
    attempt = tmp_path / "attempt-001"
    assert (attempt / "trials.jsonl").read_text() == "synthetic partial trial\n"
    assert validation.read_json(attempt / "failure.json")["status"] == "failed"
    assert not (attempt / "outcome.json").exists()
    with pytest.raises(ValueError, match="attempt exists"):
        validation.execute(tmp_path)


def test_union_coverage_and_failure_taxonomy_without_retrieval_scores():
    # Two disjoint evidence ranges; same document hit alone must not count as complete.
    case = EvaluationCase(
        "question",
        frozenset({"doc.md"}),
        evidence=(
            {"source": "doc.md", "start_char": 0, "end_char": 3, "quote": "abc"},
            {"source": "doc.md", "start_char": 6, "end_char": 9, "quote": "ghi"},
        ),
    )
    chunks = [
        Chunk(
            str(i),
            "doc",
            "doc.md",
            text,
            start,
            start + len(text),
            {"section": "same", "version": "test"},
        )
        for i, (text, start) in enumerate((("abc", 0), ("def", 3), ("ghi", 6)))
    ]
    hits = [SearchHit(c, 1) for c in chunks]
    assert complete(case, hits) and coverage(case, hits)["complete"]
    assert not complete(case, hits[:1])
    cfg = {"context_char_budget": 3}
    assert classify(case, hits[:1], hits[:1], hits[:1], chunks, cfg) == "recall_missing"
    assert classify(case, hits, hits[:1], hits[:1], chunks, cfg) == "ranking_failure"
    assert classify(case, hits, hits, hits[:1], chunks, cfg) == "context_assembly"


def test_model_lock_rejects_corrupted_file_before_model_loading(monkeypatch, tmp_path):
    for name in ("e5", "bge"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "weights").write_bytes(b"synthetic weights")
        validation.write_json(
            directory / "manifest.json",
            {"files": {"weights": {"sha256": validation.sha256_file(directory / "weights")}}},
        )
    monkeypatch.setattr(validation, "E5_DIRECTORY", tmp_path / "e5")
    monkeypatch.setattr(validation, "BGE_DIRECTORY", tmp_path / "bge")
    assert len(validation.model_locks()) == 4
    (tmp_path / "bge/weights").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="model file hash mismatch"):
        validation.model_locks()


def test_protection_keeps_snapshot_and_allows_only_recorded_current_doc_update(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "evaluation").mkdir()
    (tmp_path / "out/baseline/evaluation").mkdir(parents=True)
    source = tmp_path / "evaluation/README.md"
    source.write_text("old docs")
    before = validation.sha256_file(source)
    (tmp_path / "out/baseline/evaluation/README.md").write_text("old docs")
    out = tmp_path / "out"
    validation.write_json(out / "protected-before.json", {"evaluation/README.md": before})
    source.write_text("new docs")
    validation.write_json(
        out / "documentation-update.json",
        {
            "evaluation/README.md": {
                "before_sha256": before,
                "after_sha256": validation.sha256_file(source),
            }
        },
    )
    result = validation.verify_protected(out)
    assert result["intentional_documentation_updates"] == ["evaluation/README.md"]
    assert validation.read_json(out / "protected-before.json")["evaluation/README.md"] == before
    source.write_text("unexpected later change")
    with pytest.raises(ValueError, match="protected/locked input changed"):
        validation.verify_protected(out)


def test_protection_never_accepts_exemption_for_historical_evidence(tmp_path):
    validation.write_json(
        tmp_path / "protected-before.json", {"evaluation/reports/old.json": "original"}
    )
    validation.write_json(
        tmp_path / "documentation-update.json",
        {"evaluation/reports/old.json": {"before_sha256": "original", "after_sha256": "altered"}},
    )
    with pytest.raises(ValueError, match="only the current"):
        validation.verify_protected(tmp_path)


@pytest.fixture
def synthetic_finished_run(monkeypatch, tmp_path):
    """Synthetic records only: no actual candidate is approved or scored by this fixture."""
    from dataclasses import asdict
    from app.budget_reranking import retrieval_signals
    from scripts import report_new_topics

    config = validation.protocol_config()
    chunk = Chunk(
        "c1",
        "d1",
        "doc.md",
        "abc",
        0,
        3,
        {"section": "same", "product": "synthetic", "version": "1"},
    )
    monkeypatch.setattr(
        report_new_topics, "verify_protocol", lambda out: ({"configuration": config}, [chunk])
    )
    (tmp_path / "inputs").mkdir()
    (tmp_path / "attempt-001").mkdir()
    validation.write_json(tmp_path / "audit.json", {"synthetic": True})
    validation.write_json(tmp_path / "attempt-001/outcome.json", {"status": "completed"})
    questions, trials = [], []
    before = {
        "chunk_id": "c1",
        "score": 2 / 61,
        "dense_rank": 1,
        "sparse_rank": 1,
        "rerank_score": None,
        "rerank_rank": None,
    }
    for index, case_id in enumerate(validation.IDS):
        questions.append(
            {
                "id": case_id,
                "question": "SYNTHETIC fixture",
                "reference_answer": "abc",
                "relevant_sources": ["doc.md"],
                "filters": {"product": "synthetic", "version": "1"},
                "split": "test",
                "review_status": "human_verified",
                "reviewed_by": "Synthetic test fixture",
                "reviewed_at": "2020-01-01T00:00:00Z",
                "evidence": [{"source": "doc.md", "start_char": 0, "end_char": 3, "quote": "abc"}],
            }
        )
        order = (
            report_new_topics.STRATEGIES[index % 3 :] + report_new_topics.STRATEGIES[: index % 3]
        )
        for strategy in order:
            triggered = strategy == "all"  # Gate skips a one-candidate synthetic pool.
            after = {**before, "rerank_score": 0.7, "rerank_rank": 1} if triggered else before
            trials.append(
                {
                    "id": case_id,
                    "strategy": strategy,
                    "repeat": 0,
                    "order": list(order),
                    "before": [before],
                    "after": [after],
                    "context_ids": ["c1"],
                    "top": {"complete": True},
                    "context": {"complete": True},
                    "triggered": triggered,
                    "scored_pairs": int(triggered),
                    "signals": asdict(retrieval_signals([SearchHit(chunk, 2 / 61, 1, 1)])),
                    **dict.fromkeys(
                        ("total_ms", "retrieval_ms", "context_ms", "gate_ms", "rerank_ms"), 1.0
                    ),
                }
            )
    validation.write_jsonl(tmp_path / "inputs/questions.reviewed.jsonl", questions)
    validation.write_jsonl(tmp_path / "attempt-001/trials.jsonl", trials)
    return tmp_path, trials


def test_complete_report_recomputes_full_matrix_and_preserves_previous_report(
    synthetic_finished_run,
):
    from scripts import report_new_topics

    out, _ = synthetic_finished_run
    report_new_topics.report(out)
    result = validation.read_json(out / "analysis-complete/results.json")
    assert result["independent_coverage_checks"] == 72
    assert result["summary"]["all"]["bge_calls"] == 12
    assert result["summary"]["adaptive"]["bge_calls"] == 0
    assert result["answer_accuracy"] is None
    with pytest.raises(ValueError, match="already exists"):
        report_new_topics.report(out)


@pytest.mark.parametrize(
    "corruption", ["gold_score", "missing_trial", "order", "version", "timing"]
)
def test_report_rejects_unsupported_aggregate(synthetic_finished_run, corruption):
    from scripts import report_new_topics

    out, trials = synthetic_finished_run
    if corruption == "gold_score":
        trials[0]["top"]["complete"] = False
    elif corruption == "missing_trial":
        trials.pop()
    elif corruption == "order":
        trials.reverse()
    elif corruption == "version":
        questions = validation.rows(out / "inputs/questions.reviewed.jsonl")
        questions[0]["filters"]["version"] = "wrong"
        (out / "inputs/questions.reviewed.jsonl").unlink()
        validation.write_jsonl(out / "inputs/questions.reviewed.jsonl", questions)
    else:
        trials[0]["total_ms"] = float("nan")
    (out / "attempt-001/trials.jsonl").unlink()
    validation.write_jsonl(out / "attempt-001/trials.jsonl", trials)
    with pytest.raises(ValueError):
        report_new_topics.report(out)
    assert not (out / "analysis-complete").exists()
