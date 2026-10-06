from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.domain import Chunk, SearchHit
from app.evaluation import EvaluationCase
from app.factory import create_service
from app.generation import Completion, OpenAICompatibleGenerator
from app.verification_evaluation import evaluate_verification_pair, summarize_model_calls
from scripts.review_answers import export_review, score_review
from scripts import evaluate


TEXT = "允许凭证时必须显式指定允许的源。"


def grounded() -> str:
    return json.dumps(
        {
            "status": "answered",
            "claims": [{"text": TEXT, "evidence": [{"citation": 1, "quote": TEXT}]}],
        },
        ensure_ascii=False,
    )


def model(**kwargs) -> OpenAICompatibleGenerator:
    return OpenAICompatibleGenerator(
        model="test", api_key="test-only", base_url="http://localhost.invalid/v1", **kwargs
    )


def hits() -> list[SearchHit]:
    return [SearchHit(Chunk("c", "d", "guide.md", TEXT, 0, len(TEXT)), 1.0)]


def paired_report(tmp_path: Path, monkeypatch) -> dict:
    generator = model(verify_support=False)
    outputs = iter(
        [Completion(grounded(), 10, 5, 15), Completion('{"supported":[false]}', 20, 3, 23)]
    )
    monkeypatch.setattr(generator, "_complete", lambda *_: next(outputs))
    service = create_service(
        Settings(
            embedding_provider="hashing",
            llm_provider="extractive",
            data_dir=tmp_path / "index",
            retrieval_strategy="bm25",
            context_char_budget=100,
        )
    )
    service.generator = generator
    for version in ("v1", "v2"):
        service.ingest("guide.md", TEXT, {"product": "fastapi", "version": version})
    cases = [
        EvaluationCase(
            "允许凭证时怎么指定源？",
            frozenset({"guide.md"}),
            case_id="a",
            filters={"product": "fastapi", "version": "v1"},
        ),
        EvaluationCase(
            "允许凭证？",
            frozenset(),
            case_id="b",
            should_answer=False,
            expected_status="needs_clarification",
            filters={"product": "fastapi"},
        ),
    ]
    verified, comparison = evaluate_verification_pair(cases, service, generator, 1)
    with pytest.raises(StopIteration):
        next(outputs)
    return {
        "answers": verified,
        "verification_comparison": comparison,
        "warnings": ["Unreviewed development fixture, not an effectiveness result."],
    }


def test_pair_reuses_exact_draft_context_and_preserves_usage(tmp_path, monkeypatch):
    report = paired_report(tmp_path, monkeypatch)
    before = report["verification_comparison"]["without_verification"]
    after = report["answers"]
    assert before["details"][0]["response"]["status"] == "answered"
    assert after["details"][0]["response"]["status"] == "insufficient_evidence"
    assert before["details"][0]["response"]["context"] == after["details"][0]["response"]["context"]
    assert before["details"][0]["response"]["trace"]["context_chars"] <= 100
    assert before["details"][1]["response"]["status"] == "needs_clarification"
    assert before["model_calls"]["stages"]["generation"]["total_tokens"] == 15
    assert before["model_calls"]["stages"]["verification"]["sdk_call_count"] == 0
    assert after["model_calls"]["stages"]["generation"]["sdk_call_count"] == 1
    assert after["model_calls"]["stages"]["verification"]["total_tokens"] == 23
    assert after["answer_correctness"] is None


@pytest.mark.parametrize(
    "verdict", ["[]", "{}", '{"supported":["false"]}', '{"supported":[]}', "bad json"]
)
def test_invalid_verification_is_error_not_successful_refusal(monkeypatch, verdict):
    generator = model()
    outputs = iter([grounded(), verdict])
    monkeypatch.setattr(generator, "_complete", lambda *_: next(outputs))
    result = generator.generate("凭证", hits())
    assert result.status == "error"
    assert result.reason == "verification_response_invalid"
    assert result.model_trace["calls"][-1]["status"] == "invalid_response"


def test_timeout_keeps_trace_without_exposing_exception_text(monkeypatch):
    generator = model()

    def complete(system, user):
        if "核验" in system[:8]:
            raise TimeoutError("PRIVATE REQUEST DETAILS")
        return Completion(grounded(), 10, 5, 15)

    monkeypatch.setattr(generator, "_complete", complete)
    result = generator.generate("凭证", hits())
    assert result.status == "error"
    assert result.reason == "TimeoutError"
    serialized = json.dumps(result.__dict__)
    assert "PRIVATE" not in serialized
    assert len(result.model_trace["calls"]) == 2
    metrics = summarize_model_calls([{"response": {"model_trace": result.model_trace}}])
    assert metrics["stages"]["verification"]["total_tokens"] is None
    assert metrics["stages"]["verification"]["error_count"] == 1


def test_request_usage_is_isolated_under_concurrency(monkeypatch):
    generator = model(verify_support=False)
    barrier = Barrier(2)

    def complete(system, user):
        barrier.wait(timeout=5)
        tokens = 17 if user.endswith("甲") else 29
        return Completion(grounded(), tokens, 5, tokens + 5)

    monkeypatch.setattr(generator, "_complete", complete)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda q: generator.generate(q, hits()), ["甲", "乙"]))
    assert [item.model_trace["calls"][0]["prompt_tokens"] for item in results] == [17, 29]
    assert all(len(item.model_trace["calls"]) == 1 for item in results)


@pytest.mark.parametrize(
    "usage, expected",
    [(None, None), (SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10), 10)],
)
def test_sdk_usage_is_read_without_inventing_missing_tokens(monkeypatch, usage, expected):
    generator = model(verify_support=False)
    monkeypatch.setattr(
        generator.client.chat.completions,
        "create",
        lambda **kwargs: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=grounded()))],
            usage=usage,
        ),
    )
    result = generator.generate("凭证", hits())
    assert result.model_trace["calls"][0]["total_tokens"] == expected


def save_fixture(tmp_path, monkeypatch):
    report = paired_report(tmp_path, monkeypatch)
    path = tmp_path / "paired.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    reviews = tmp_path / "reviews.jsonl"
    assert export_review(path, reviews) == 1
    return path, reviews


def test_pending_reviews_never_create_accuracy_and_do_not_overwrite(tmp_path, monkeypatch):
    path, reviews = save_fixture(tmp_path, monkeypatch)
    metrics = score_review(path, reviews)
    assert metrics["reviewed_count"] == 0
    assert metrics["draft_correctness_on_reviewed"]["rate"] is None
    with pytest.raises(ValueError, match="已存在"):
        export_review(path, reviews)


def test_human_score_reports_subset_denominators_and_keeps_raw_report(tmp_path, monkeypatch):
    path, reviews = save_fixture(tmp_path, monkeypatch)
    original = path.read_bytes()
    row = json.loads(reviews.read_text(encoding="utf-8"))
    row.update(
        review_status="human_verified",
        answer_correct=True,
        all_claims_supported=True,
        reviewed_by="unit-test-fixture",
        reviewed_at="2026-09-12T16:00:00+08:00",
    )
    reviews.write_text(json.dumps(row), encoding="utf-8")
    metrics = score_review(path, reviews)
    assert metrics["coverage"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert metrics["correct_and_supported_drafts_blocked"]["rate"] == 1.0
    assert metrics["kept_correctness_on_reviewed"]["rate"] is None
    assert path.read_bytes() == original


@pytest.mark.parametrize("change", ["report", "draft", "duplicate", "bool", "timestamp"])
def test_rejects_stale_or_invalid_human_judgments(tmp_path, monkeypatch, change):
    path, reviews = save_fixture(tmp_path, monkeypatch)
    row = json.loads(reviews.read_text(encoding="utf-8"))
    row.update(
        review_status="human_verified",
        answer_correct=True,
        all_claims_supported=True,
        reviewed_by="unit-test-fixture",
        reviewed_at="2026-09-12T16:00:00+08:00",
    )
    if change == "report":
        path.write_bytes(path.read_bytes() + b"\n")
    elif change == "draft":
        row["draft_sha256"] = "stale"
    elif change == "bool":
        row["answer_correct"] = "true"
    elif change == "timestamp":
        row["reviewed_at"] = "2026-09-12"
    rows = [row, deepcopy(row)] if change == "duplicate" else [row]
    reviews.write_text("\n".join(json.dumps(item) for item in rows), encoding="utf-8")
    with pytest.raises(ValueError):
        score_review(path, reviews)


def test_cli_pair_writes_report_and_exports_review_without_network(tmp_path, monkeypatch):
    documents = tmp_path / "docs"
    documents.mkdir()
    (documents / "guide.md").write_text(TEXT, encoding="utf-8")
    questions = tmp_path / "questions.jsonl"
    questions.write_text(
        json.dumps(
            {
                "id": "q",
                "question": "允许凭证时怎么指定源？",
                "relevant_sources": ["guide.md"],
                "should_answer": True,
                "split": "development",
                "reference_answer": TEXT,
                "evidence": [
                    {"source": "guide.md", "quote": TEXT, "start_char": 0, "end_char": len(TEXT)}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    settings = Settings(
        embedding_provider="hashing",
        llm_provider="openai_compatible",
        llm_api_key="test-only",
        llm_base_url="https://localhost.invalid/v1",
        data_dir=tmp_path / "index",
    )
    monkeypatch.setattr(evaluate, "Settings", lambda: settings)
    outputs = iter(
        [Completion(grounded(), 10, 5, 15), Completion('{"supported":[true]}', 20, 3, 23)]
    )
    monkeypatch.setattr(OpenAICompatibleGenerator, "_complete", lambda *_: next(outputs))
    output_dir = tmp_path / "reports"
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate",
            "--documents",
            str(documents),
            "--questions",
            str(questions),
            "--split",
            "development",
            "--strategies",
            "bm25",
            "--top-k",
            "1",
            "--answers",
            "--answer-strategy",
            "bm25",
            "--compare-verification",
            "--context-char-budget",
            "100",
            "--output-dir",
            str(output_dir),
            "--report-name",
            "fixture",
        ],
    )
    evaluate.main()
    path = output_dir / "fixture.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["configuration"]["verification_mode"] == "paired"
    assert report["configuration"]["context_char_budget"] == 100
    assert report["answers"]["details"][0]["response"]["status"] == "answered"
    assert report["provenance"]["selected_case_ids"] == ["q"]
    assert "同一答案的核验开关对照" in path.with_suffix(".md").read_text(encoding="utf-8")
    assert export_review(path, tmp_path / "review.jsonl") == 1
    with pytest.raises(ValueError, match="配对报告已存在"):
        evaluate.main()


@pytest.mark.parametrize(
    "extra", [["--demo", "--answers", "--split", "development"], ["--answers", "--split", "test"]]
)
def test_cli_rejects_ineligible_pair_before_model_access(monkeypatch, extra):
    monkeypatch.setattr("sys.argv", ["evaluate", "--compare-verification", *extra])
    with pytest.raises(ValueError, match="配对核验实验"):
        evaluate.main()
