"""Offline unit tests for the LLM-as-Judge answer evaluation and calibration pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.chunking import TextChunker
from app.embeddings import HashingEmbedding
from app.generation import Completion, ExtractiveGenerator
from app.judge import (
    BoundedDeepSeekGenerator,
    BudgetStopped,
    DeepSeekJudge,
    JudgeVerdict,
    TokenBudgetTracker,
    build_judge_payload,
    compute_binary_confusion,
    resolve_deepseek_api_key,
    summarize_calibration_group,
)
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever
from app.service import RAGService
from app.storage import JsonChunkRepository
from scripts.evaluate_answers_judge import (
    DEFAULT_OLD_HUMAN_REVIEWS,
    DEFAULT_OLD_REPORT,
    DEFAULT_V012_DIR,
    build_calibration_dataset,
    execute,
    load_reviewed_development_cases,
    prepare,
    report,
)


class FakeOpenAIClient:
    def __init__(self, *, models=("deepseek-flash",), **kwargs):
        self.models = SimpleNamespace(
            list=lambda: SimpleNamespace(data=[SimpleNamespace(id=name) for name in models])
        )
        self.closed = False

    def with_options(self, **kwargs):
        return self

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    "env_map,expected",
    [
        ({"DEEPSEEK_API_KEY": "test-key-upper"}, "test-key-upper"),
        ({"Deepseek_API_key": "test-key-mixed"}, "test-key-mixed"),
        ({"deepseek_api_key": "test-key-lower"}, "test-key-lower"),
        ({"OTHER_KEY": "none"}, ""),
    ],
)
def test_resolve_deepseek_api_key_case_insensitive(env_map, expected):
    assert resolve_deepseek_api_key(env_map, check_registry=False) == expected


def test_judge_verdict_strict_pydantic_validation():
    valid = JudgeVerdict.model_validate_json(
        json.dumps(
            {
                "correctness": "correct",
                "faithfulness": True,
                "unsupported_claims": [],
                "behavior_matched": True,
                "core_question_answered": True,
                "reasoning": "完全符合参考答案且有原文依据。",
            },
            ensure_ascii=False,
        )
    )
    assert valid.correctness == "correct"
    assert valid.faithfulness is True

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        JudgeVerdict.model_validate_json(
            json.dumps(
                {
                    "correctness": "correct",
                    "faithfulness": True,
                    "unsupported_claims": [],
                    "behavior_matched": True,
                    "core_question_answered": True,
                    "reasoning": "ok",
                    "extra_field": 1,
                }
            )
        )

    # Faithfulness=True with non-empty unsupported_claims rejected
    with pytest.raises(ValidationError):
        JudgeVerdict.model_validate_json(
            json.dumps(
                {
                    "correctness": "correct",
                    "faithfulness": True,
                    "unsupported_claims": ["幻觉结论"],
                    "behavior_matched": True,
                    "core_question_answered": True,
                    "reasoning": "矛盾",
                },
                ensure_ascii=False,
            )
        )

    # Faithfulness=False with empty unsupported_claims rejected
    with pytest.raises(ValidationError):
        JudgeVerdict.model_validate_json(
            json.dumps(
                {
                    "correctness": "incorrect",
                    "faithfulness": False,
                    "unsupported_claims": [],
                    "behavior_matched": True,
                    "core_question_answered": False,
                    "reasoning": "缺少未支持列表",
                },
                ensure_ascii=False,
            )
        )


def test_judge_single_documented_retry_and_no_silent_retries(tmp_path, monkeypatch):
    monkeypatch.setattr("openai.OpenAI", FakeOpenAIClient)
    budget = TokenBudgetTracker(
        hard_token_limit=10_000,
        max_completion_calls=10,
        journal_path=tmp_path / "journal.jsonl",
    )
    gen = BoundedDeepSeekGenerator(api_key="fixture-key", budget=budget)
    judge = DeepSeekJudge(gen, max_parse_retries=1)

    responses = iter(
        [
            Completion("not-valid-json", 10, 5, 15, "deepseek-flash", "stop"),
            Completion(
                json.dumps(
                    {
                        "correctness": "correct",
                        "faithfulness": True,
                        "unsupported_claims": [],
                        "behavior_matched": True,
                        "core_question_answered": True,
                        "reasoning": "第二次重试输出合法 JSON。",
                    },
                    ensure_ascii=False,
                ),
                12,
                8,
                20,
                "deepseek-flash",
                "stop",
            ),
        ]
    )
    monkeypatch.setattr(
        "app.deepseek.DeepSeekGenerator._complete",
        lambda self, sys_p, usr_p: next(responses),
    )

    result = judge.evaluate(phase="execute", job="judge/case-1", payload={"case_id": "case-1"})
    assert result["status"] == "ok_after_retry"
    assert result["attempt_count"] == 2
    assert result["retry_count"] == 1
    assert result["parse_failure_count"] == 1
    assert result["verdict"]["correctness"] == "correct"
    assert budget.calls == 2
    assert budget.total_tokens == 35

    # Now verify two consecutive parse failures stop after 1 retry (2 total attempts)
    bad_responses = iter(
        [
            Completion('{"bad": 1}', 10, 5, 15, "deepseek-flash", "stop"),
            Completion('{"still_bad": 2}', 10, 5, 15, "deepseek-flash", "stop"),
            Completion('{"never_called": 3}', 10, 5, 15, "deepseek-flash", "stop"),
        ]
    )
    monkeypatch.setattr(
        "app.deepseek.DeepSeekGenerator._complete",
        lambda self, sys_p, usr_p: next(bad_responses),
    )
    failed = judge.evaluate(phase="execute", job="judge/case-2", payload={"case_id": "case-2"})
    assert failed["status"] == "parse_error"
    assert failed["verdict"] is None
    assert failed["attempt_count"] == 2
    assert failed["retry_count"] == 1
    assert failed["parse_failure_count"] == 2
    assert budget.calls == 4


def test_hard_token_budget_stops_before_exceeding(tmp_path, monkeypatch):
    monkeypatch.setattr("openai.OpenAI", FakeOpenAIClient)
    budget = TokenBudgetTracker(
        hard_token_limit=100,
        max_completion_calls=10,
        journal_path=tmp_path / "journal.jsonl",
    )
    gen = BoundedDeepSeekGenerator(api_key="fixture-key", budget=budget)
    monkeypatch.setattr(
        "app.deepseek.DeepSeekGenerator._complete",
        lambda self, sys_p, usr_p: Completion("{}", 80, 25, 105, "deepseek-flash", "stop"),
    )
    gen._complete("sys", "user")
    assert budget.total_tokens == 105
    assert budget.stopped is True
    with pytest.raises(BudgetStopped):
        gen._complete("sys", "user")
    assert budget.calls == 1


@pytest.mark.parametrize(
    "split,review_status",
    [
        ("test", "human_verified"),
        ("development", "unreviewed"),
    ],
)
def test_rejects_held_out_or_unreviewed_questions(tmp_path, split, review_status):
    q_file = tmp_path / "questions.jsonl"
    row = {
        "id": "case-001",
        "category": "cors",
        "group_id": "cors",
        "question": "localhost 端口不同是同一源吗？",
        "reference_answer": "不是。",
        "should_answer": True,
        "expected_status": "answered",
        "filters": {"product": "fastapi", "version": "0.110.0"},
        "relevant_sources": ["fastapi/0.110.0/tutorial/cors.md"],
        "evidence": [
            {
                "source": "fastapi/0.110.0/tutorial/cors.md",
                "quote": "源是协议、域和端口的组合。",
                "start_char": 0,
                "end_char": 13,
            }
        ],
        "split": split,
        "review_status": review_status,
        "reviewed_by": "tester",
        "reviewed_at": "2026-10-28T00:00:00+00:00",
    }
    q_file.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="仅允许全部已人工确认的开发集"):
        load_reviewed_development_cases(q_file)


@pytest.mark.skipif(
    not (DEFAULT_OLD_HUMAN_REVIEWS.exists() and DEFAULT_V012_DIR.exists()),
    reason="Requires local calibration artifacts in work/",
)
def test_real_calibration_dataset_counts_and_separation():
    items = build_calibration_dataset(
        old_report_path=DEFAULT_OLD_REPORT,
        old_human_reviews_path=DEFAULT_OLD_HUMAN_REVIEWS,
        v012_dir=DEFAULT_V012_DIR,
    )
    human = [x for x in items if x["group"] == "human"]
    assistant = [x for x in items if x["group"] == "assistant"]
    synthetic = [x for x in items if x["group"] == "synthetic_negative_control"]

    assert len(human) == 11
    assert sum(x["subgroup"] == "human_old_v010" for x in human) == 6
    assert sum(x["subgroup"] == "human_new_v012" for x in human) == 5
    assert all(x["reviewed_by"] == "JQH" for x in human)

    assert len(assistant) == 12
    assert all(x["reviewed_by"] == "Codex" for x in assistant)
    assert sum(not x["label_answer_correct"] for x in assistant) == 1  # R015

    assert len(synthetic) == 3
    assert all(
        not x["label_answer_correct"] and not x["label_all_claims_supported"] for x in synthetic
    )

    # Verify build_judge_payload strips reviewer labels
    sample_payload = build_judge_payload(
        case_id=human[0]["case_id"],
        question=human[0]["question"],
        filters=human[0]["filters"],
        expected_status=human[0]["expected_status"],
        reference_answer=human[0]["reference_answer"],
        gold_evidence=human[0]["gold_evidence"],
        response=human[0]["response"],
    )
    serialized = json.dumps(sample_payload, ensure_ascii=False)
    assert "JQH" not in serialized
    assert "label_answer_correct" not in serialized


def test_calibration_confusion_and_group_summary():
    confusion = compute_binary_confusion([(True, True), (True, False), (False, False)])
    assert confusion["n"] == 3
    assert confusion["agreed"] == 2
    assert confusion["confusion"] == {
        "tp_ref_true_pred_true": 1,
        "fp_ref_false_pred_true": 0,
        "tn_ref_false_pred_false": 1,
        "fn_ref_true_pred_false": 1,
    }

    group_summary = summarize_calibration_group(
        [
            {
                "calibration_id": "v012:R015",
                "case_id": "support-011",
                "label_answer_correct": False,
                "label_all_claims_supported": True,
                "judge": {
                    "status": "ok",
                    "retry_count": 0,
                    "verdict": {
                        "correctness": "incorrect",
                        "faithfulness": True,
                        "unsupported_claims": [],
                        "behavior_matched": True,
                        "core_question_answered": True,
                        "reasoning": "第3条关于Starlette响应头的扩展结论与版本事实冲突。",
                    },
                },
            }
        ]
    )
    assert group_summary["correctness_strict_agreement"]["agreed"] == 1
    assert group_summary["faithfulness_agreement"]["agreed"] == 1
    assert group_summary["joint_strict_agreement"]["agreed"] == 1
    assert group_summary["disagreements"] == []


@pytest.mark.skipif(
    not (DEFAULT_OLD_HUMAN_REVIEWS.exists() and DEFAULT_V012_DIR.exists()),
    reason="Requires local calibration artifacts in work/",
)
def test_offline_end_to_end_prepare_execute_calibrate_and_report(tmp_path, monkeypatch):
    monkeypatch.setattr("openai.OpenAI", FakeOpenAIClient)

    docs_dir = tmp_path / "corpus"
    (docs_dir / "fastapi" / "0.110.0" / "tutorial").mkdir(parents=True)
    (docs_dir / "fastapi" / "0.115.0" / "tutorial").mkdir(parents=True)
    quote = "源是协议（http，https）、域以及端口（80、443、8080）的组合。"
    (docs_dir / "fastapi" / "0.110.0" / "tutorial" / "cors.md").write_text(
        f"# CORS\n\n{quote}\n", encoding="utf-8"
    )
    (docs_dir / "fastapi" / "0.115.0" / "tutorial" / "cors.md").write_text(
        f"# CORS\n\n{quote}\n", encoding="utf-8"
    )

    q_path = tmp_path / "questions.reviewed.jsonl"
    questions = [
        {
            "id": "support-001",
            "category": "cors",
            "group_id": "cors",
            "question": "localhost 的端口不同，浏览器会把它们视为同一个源吗？",
            "reference_answer": "不会，源由协议、域和端口共同组成。",
            "should_answer": True,
            "expected_status": "answered",
            "filters": {"product": "fastapi", "version": "0.110.0"},
            "relevant_sources": ["fastapi/0.110.0/tutorial/cors.md"],
            "evidence": [
                {
                    "source": "fastapi/0.110.0/tutorial/cors.md",
                    "quote": quote,
                    "start_char": 8,
                    "end_char": 8 + len(quote),
                }
            ],
            "split": "development",
            "review_status": "human_verified",
            "reviewed_by": "JQH",
            "reviewed_at": "2026-10-28T04:23:28+00:00",
        },
        {
            "id": "support-unanswerable-01",
            "category": "needs_clarification",
            "group_id": "scope-1",
            "question": "CORS 请求失败，应该怎么设置？",
            "reference_answer": "需要补充文档版本。",
            "should_answer": False,
            "expected_status": "needs_clarification",
            "filters": {"product": "fastapi"},
            "relevant_sources": [],
            "evidence": [],
            "split": "development",
            "review_status": "human_verified",
            "reviewed_by": "JQH",
            "reviewed_at": "2026-10-28T04:43:12+00:00",
        },
    ]
    q_path.write_text(
        "\n".join(json.dumps(q, ensure_ascii=False) for q in questions) + "\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "exp-judge"
    report_prefix = tmp_path / "reports" / "judge-report"

    args = argparse.Namespace(
        output=out_dir,
        questions=q_path,
        documents=docs_dir,
        old_report=DEFAULT_OLD_REPORT,
        old_human_reviews=DEFAULT_OLD_HUMAN_REVIEWS,
        v012_dir=DEFAULT_V012_DIR,
        seed_vector_cache=None,
        model_dir=Path("work/models/multilingual-e5-small"),
        skip_e5_prewarm=True,
        with_calibrate=True,
        report_prefix=report_prefix,
    )
    prep_info = prepare(args)
    assert prep_info["question_counts"]["total"] == 2

    def fake_service_factory(out: Path, protocol: dict) -> RAGService:
        repo = JsonChunkRepository(out / "index")
        retriever = HybridRetriever(DenseRetriever(HashingEmbedding()), BM25Retriever())
        return RAGService(
            chunker=TextChunker(500, 80, strategy="sections"),
            retriever=retriever,
            generator=ExtractiveGenerator(),
            repository=repo,
            retrieval_strategy="hybrid",
            context_char_budget=8000,
            context_policy="neighbors",
        )

    def fake_complete(self, system_prompt: str, user_prompt: str) -> Completion:
        if self.current_stage == "generation":
            return Completion(
                json.dumps(
                    {
                        "status": "answered",
                        "claims": [
                            {
                                "text": "不会，端口不同属于不同源。",
                                "evidence": [{"citation": 1, "quote": quote}],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                100,
                30,
                130,
                "deepseek-flash",
                "stop",
            )
        if self.current_stage == "verification":
            return Completion('{"supported": [true]}', 80, 10, 90, "deepseek-flash", "stop")
        payload = json.loads(user_prompt)
        is_mutated = (
            "会，localhost 的端口不同仍会被视为同一个源" in payload.get("system_answer", "")
            or "必须先执行完成" in payload.get("system_answer", "")
            or "始终把上传内容全部保存在内存中" in payload.get("system_answer", "")
        )
        is_r015 = "它们之间的唯一区别是" in json.dumps(
            payload.get("system_claims", []), ensure_ascii=False
        )
        if is_mutated:
            verdict = {
                "correctness": "incorrect",
                "faithfulness": False,
                "unsupported_claims": ["与上下文矛盾的合成结论"],
                "behavior_matched": True,
                "core_question_answered": False,
                "reasoning": "结论与上下文直接矛盾。",
            }
        elif is_r015:
            verdict = {
                "correctness": "incorrect",
                "faithfulness": True,
                "unsupported_claims": [],
                "behavior_matched": True,
                "core_question_answered": True,
                "reasoning": "核心结论正确且有上下文引文，但Starlette响应头扩展结论与版本事实冲突。",
            }
        else:
            verdict = {
                "correctness": "correct",
                "faithfulness": True,
                "unsupported_claims": [],
                "behavior_matched": True,
                "core_question_answered": True,
                "reasoning": "回答准确且所有结论均有上下文支持。",
            }
        return Completion(
            json.dumps(verdict, ensure_ascii=False), 120, 40, 160, "deepseek-flash", "stop"
        )

    monkeypatch.setattr("app.deepseek.DeepSeekGenerator._complete", fake_complete)

    def fake_generator_factory(budget: TokenBudgetTracker) -> BoundedDeepSeekGenerator:
        return BoundedDeepSeekGenerator(
            api_key="fixture-key",
            budget=budget,
            verify_support=True,
        )

    exec_info = execute(
        args,
        service_factory=fake_service_factory,
        generator_factory=fake_generator_factory,
    )
    assert exec_info["executed_question_count"] == 2
    assert exec_info["calibrated_item_count"] == 26

    summary = report(args)
    assert summary["end_to_end_evaluation"]["answerable_questions"]["correctness_counts"] == {
        "correct": 1,
        "partially_correct": 0,
        "incorrect": 0,
    }
    assert (
        summary["end_to_end_evaluation"]["clarification_and_refusal_questions"]["correct_rate"][
            "numerator"
        ]
        == 1
    )
    assert (
        summary["judge_calibration"]["vs_human_all"]["correctness_strict_agreement"]["agreed"] == 11
    )
    assert (
        summary["judge_calibration"]["vs_assistant_v012"]["correctness_strict_agreement"]["agreed"]
        == 12
    )
    assert (
        summary["judge_calibration"]["vs_synthetic_negative_controls"]["faithfulness_agreement"][
            "agreed"
        ]
        == 3
    )
    assert report_prefix.with_suffix(".json").is_file()
    assert report_prefix.with_suffix(".md").is_file()
