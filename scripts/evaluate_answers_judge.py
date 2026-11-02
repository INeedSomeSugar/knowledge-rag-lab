"""Freeze, execute, calibrate, and report end-to-end RAG answer evaluation with an LLM-as-Judge."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.answers import parse_grounded_answer  # noqa: E402
from app.chunking import TextChunker  # noqa: E402
from app.config import Settings  # noqa: E402
from app.evaluation import EvaluationCase, evidence_covered, load_evaluation_cases  # noqa: E402
from app.factory import create_cached_embedding  # noqa: E402
from app.generation import SYSTEM_PROMPT, ExtractiveGenerator  # noqa: E402
from app.judge import (  # noqa: E402
    JUDGE_RUBRIC_VERSION,
    JUDGE_SYSTEM_PROMPT,
    BoundedDeepSeekGenerator,
    DeepSeekJudge,
    JudgeVerdict,
    TokenBudgetTracker,
    build_judge_payload,
    summarize_calibration_group,
)
from app.local_embeddings import E5_DIRECTORY, E5_MODEL, E5_REVISION  # noqa: E402
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever  # noqa: E402
from app.service import RAGService, answer_fields  # noqa: E402
from app.storage import JsonChunkRepository  # noqa: E402
from scripts.compare_generation import CONTROLS, MUTATIONS, hits_from_response  # noqa: E402
from scripts.evaluate import load_corpus, validate_evidence, validate_relevant_sources  # noqa: E402
from scripts.review_answers import draft_digest, load_pairs, report_digest  # noqa: E402


DEFAULT_QUESTIONS = (
    ROOT / "work/human-review/20261028/review-corrections-20261028T075633Z/questions.current.jsonl"
)
REVISION1_QUESTIONS = (
    ROOT / "work/human-review/20261028/reviewed-retrieval-20261028T044628Z/questions.reviewed.jsonl"
)
DEFAULT_DOCUMENTS = ROOT / "evaluation/support_corpus_large"
DEFAULT_OLD_REPORT = ROOT / "evaluation/reports/deepseek-bm25-development-v010.json"
DEFAULT_OLD_HUMAN_REVIEWS = ROOT / "work/human-review/20261028/03-初始答案.pending.jsonl"
DEFAULT_V012_DIR = ROOT / "work/experiments/generation-verification-dev-20261028-v012"
DEFAULT_SEED_VECTOR_CACHE = ROOT / "work/experiments/corpus-large-v013/vectors.sqlite3"
DEFAULT_OUTPUT_DIR = ROOT / "work/experiments/answer-judge-20261102"
DEFAULT_REPORT_PREFIX = ROOT / "evaluation/reports/answer-judge-20261102"

PIPELINE_CODE_FILES = (
    "app/answers.py",
    "app/chunking.py",
    "app/config.py",
    "app/context.py",
    "app/conversation.py",
    "app/corpus.py",
    "app/deepseek.py",
    "app/domain.py",
    "app/embeddings.py",
    "app/evaluation.py",
    "app/factory.py",
    "app/generation.py",
    "app/judge.py",
    "app/local_embeddings.py",
    "app/retrieval.py",
    "app/service.py",
    "app/storage.py",
    "app/vector_cache.py",
    "app/verification_evaluation.py",
    "scripts/evaluate_answers_judge.py",
)


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_exclusive(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_json_overwrite(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def pipeline_code_hashes(root: Path = ROOT) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in PIPELINE_CODE_FILES:
        file_path = root / rel
        if file_path.is_file():
            hashes[rel] = sha256_path(file_path)
    return hashes


def pipeline_code_fingerprint(root: Path = ROOT) -> str:
    items = sorted(pipeline_code_hashes(root).items())
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode("utf-8")).hexdigest()


def load_reviewed_development_cases(path: Path) -> list[EvaluationCase]:
    cases = load_evaluation_cases(path)
    if not cases or any(
        case.split != "development" or case.review_status != "human_verified" for case in cases
    ):
        raise ValueError("仅允许全部已人工确认的开发集问题，严禁未审题或保留测试集（test split）")
    return cases


def build_calibration_dataset(
    *,
    old_report_path: Path,
    old_human_reviews_path: Path,
    v012_dir: Path,
) -> list[dict[str, object]]:
    """Load and hash-verify the 11 human-reviewed answers, 12 assistant-reviewed answers, and 3 synthetic negative controls."""
    calibration_items: list[dict[str, object]] = []

    # 1. 6 old human-reviewed answers (v010 BM25)
    _, old_before, _ = load_pairs(old_report_path)
    old_report_sha = report_digest(old_report_path)
    for row in read_jsonl(old_human_reviews_path):
        case_id = str(row["id"])
        detail = old_before[case_id]
        if row.get("report_sha256") != old_report_sha or row.get("draft_sha256") != draft_digest(
            detail
        ):
            raise ValueError(f"旧人工评分哈希不匹配：{case_id}")
        if row.get("review_status") != "human_verified":
            raise ValueError(f"旧人工评分状态非 human_verified：{case_id}")
        calibration_items.append(
            {
                "calibration_id": f"old:{case_id}",
                "group": "human",
                "subgroup": "human_old_v010",
                "case_id": case_id,
                "arm": "bm25",
                "reviewer_kind": "human",
                "reviewed_by": str(row["reviewed_by"]),
                "reviewed_at": str(row["reviewed_at"]),
                "review_status": "human_verified",
                "report_sha256": old_report_sha,
                "draft_sha256": draft_digest(detail),
                "label_answer_correct": bool(row["answer_correct"]),
                "label_all_claims_supported": bool(row["all_claims_supported"]),
                "label_notes": str(row.get("notes", "")),
                "question": str(detail["response"]["question"]),
                "filters": dict(detail.get("filters", {})),
                "expected_status": str(detail.get("expected_status", "answered")),
                "reference_answer": str(detail["reference_answer"]),
                "gold_evidence": list(detail.get("gold_evidence", [])),
                "response": deepcopy(detail["response"]),
            }
        )

    # 2. 5 new human-reviewed answers (v012 blind review) + 12 assistant-reviewed answers
    mapping = json.loads((v012_dir / "answers.blind.mapping.json").read_text(encoding="utf-8"))
    v012_reports: dict[str, tuple[str, dict[str, dict]]] = {}
    for arm in ("bm25", "e5_hybrid"):
        r_path = v012_dir / f"{arm}.report.json"
        _, before_map, _ = load_pairs(r_path)
        v012_reports[arm] = (report_digest(r_path), before_map)

    assistant_by_rid = {
        str(row["review_id"]): row
        for row in read_jsonl(
            v012_dir / "assistant-review-20261028T071527Z" / "assistant-reviews.jsonl"
        )
    }

    for row in read_jsonl(v012_dir / "answers.blind.pending.jsonl"):
        rid = str(row["review_id"])
        case_id = str(row["id"])
        arm = str(mapping[rid]["arm"])
        expected_report_sha, before_map = v012_reports[arm]
        detail = before_map[case_id]
        expected_draft_sha = draft_digest(detail)
        if (
            row.get("report_sha256") != expected_report_sha
            or row.get("draft_sha256") != expected_draft_sha
        ):
            raise ValueError(f"v012 盲审记录哈希不匹配：{rid}")

        if row.get("review_status") == "human_verified":
            calibration_items.append(
                {
                    "calibration_id": f"v012:{rid}",
                    "group": "human",
                    "subgroup": "human_new_v012",
                    "case_id": case_id,
                    "arm": arm,
                    "reviewer_kind": "human",
                    "reviewed_by": str(row["reviewed_by"]),
                    "reviewed_at": str(row["reviewed_at"]),
                    "review_status": "human_verified",
                    "report_sha256": expected_report_sha,
                    "draft_sha256": expected_draft_sha,
                    "label_answer_correct": bool(row["answer_correct"]),
                    "label_all_claims_supported": bool(row["all_claims_supported"]),
                    "label_notes": str(row.get("notes", "")),
                    "question": str(detail["response"]["question"]),
                    "filters": dict(detail.get("filters", {})),
                    "expected_status": str(detail.get("expected_status", "answered")),
                    "reference_answer": str(detail["reference_answer"]),
                    "gold_evidence": list(detail.get("gold_evidence", [])),
                    "response": deepcopy(detail["response"]),
                }
            )
        else:
            if rid not in assistant_by_rid:
                raise ValueError(f"未找到助手复核记录：{rid}")
            ai_row = assistant_by_rid[rid]
            if (
                ai_row.get("report_sha256") != expected_report_sha
                or ai_row.get("draft_sha256") != expected_draft_sha
                or ai_row.get("review_status") != "assistant_reviewed"
            ):
                raise ValueError(f"助手复核记录哈希或状态不匹配：{rid}")
            calibration_items.append(
                {
                    "calibration_id": f"v012:{rid}",
                    "group": "assistant",
                    "subgroup": "assistant_v012",
                    "case_id": case_id,
                    "arm": arm,
                    "reviewer_kind": "ai_assistant",
                    "reviewed_by": str(ai_row["reviewed_by"]),
                    "reviewed_at": str(ai_row["reviewed_at"]),
                    "review_status": "assistant_reviewed",
                    "report_sha256": expected_report_sha,
                    "draft_sha256": expected_draft_sha,
                    "label_answer_correct": bool(ai_row["answer_correct"]),
                    "label_all_claims_supported": bool(ai_row["all_claims_supported"]),
                    "label_core_question_correct": bool(
                        ai_row.get("core_question_answer_correct", ai_row["answer_correct"])
                    ),
                    "label_notes": str(ai_row.get("reason", "")),
                    "question": str(detail["response"]["question"]),
                    "filters": dict(detail.get("filters", {})),
                    "expected_status": str(detail.get("expected_status", "answered")),
                    "reference_answer": str(detail["reference_answer"]),
                    "gold_evidence": list(detail.get("gold_evidence", [])),
                    "response": deepcopy(detail["response"]),
                }
            )

    # 3. 3 synthetic contradiction negative controls (kept strictly separate from human and assistant)
    for case_id in CONTROLS:
        detail = old_before[case_id]
        base_resp = deepcopy(detail["response"])
        mutated_claims = deepcopy(base_resp["claims"])
        mutated_claims[0]["text"] = MUTATIONS[case_id]
        hits = hits_from_response(base_resp)
        mutated_draft = parse_grounded_answer(
            json.dumps({"status": "answered", "claims": mutated_claims}, ensure_ascii=False),
            hits,
        )
        if mutated_draft.status != "answered":
            raise ValueError(f"合成负样本必须保留合法逐字引用：{case_id}")
        mutated_resp = {
            **base_resp,
            **answer_fields(mutated_draft, hits),
        }
        calibration_items.append(
            {
                "calibration_id": f"synthetic_mutated:{case_id}",
                "group": "synthetic_negative_control",
                "subgroup": "synthetic_contradiction_v010",
                "case_id": case_id,
                "arm": "bm25_mutated",
                "reviewer_kind": "synthetic_mutation",
                "reviewed_by": "programmatic_contradiction",
                "reviewed_at": "2026-10-28T05:19:31+00:00",
                "review_status": "synthetic_negative_control",
                "report_sha256": old_report_sha,
                "draft_sha256": draft_digest(detail),
                "label_answer_correct": False,
                "label_all_claims_supported": False,
                "label_notes": f"将首条结论反转为与原文矛盾：{MUTATIONS[case_id]}",
                "question": str(detail["response"]["question"]),
                "filters": dict(detail.get("filters", {})),
                "expected_status": str(detail.get("expected_status", "answered")),
                "reference_answer": str(detail["reference_answer"]),
                "gold_evidence": list(detail.get("gold_evidence", [])),
                "response": mutated_resp,
            }
        )

    human_count = sum(item["group"] == "human" for item in calibration_items)
    assistant_count = sum(item["group"] == "assistant" for item in calibration_items)
    synthetic_count = sum(
        item["group"] == "synthetic_negative_control" for item in calibration_items
    )
    if (human_count, assistant_count, synthetic_count) != (11, 12, 3):
        raise ValueError(
            f"校准集数量异常：human={human_count}, assistant={assistant_count}, synthetic={synthetic_count}"
        )
    return calibration_items


def create_rag_settings(index_dir: Path, model_dir: Path = ROOT / E5_DIRECTORY) -> Settings:
    return Settings(
        embedding_provider="local_e5",
        embedding_model=E5_MODEL,
        embedding_model_revision=E5_REVISION,
        embedding_model_dir=model_dir,
        embedding_device="cpu",
        embedding_cpu_threads=4,
        embedding_batch_size=8,
        embedding_max_length=512,
        embedding_cache_revision="1",
        llm_provider="deepseek",
        llm_verify_support=True,
        chunking_strategy="sections",
        chunk_size=500,
        chunk_overlap=80,
        retrieval_top_k=5,
        retrieval_strategy="hybrid",
        reranker_provider="none",
        context_char_budget=8000,
        context_policy="neighbors",
        data_dir=index_dir,
    )


def prepare(args: argparse.Namespace) -> dict[str, object]:
    out = args.output.resolve()
    questions_path = args.questions.resolve()
    documents_dir = args.documents.resolve()
    old_report_path = args.old_report.resolve()
    old_human_path = args.old_human_reviews.resolve()
    v012_dir = args.v012_dir.resolve()

    cases = load_reviewed_development_cases(questions_path)
    validate_evidence(cases, documents_dir)
    chunker = TextChunker(500, 80, strategy="sections")
    chunks, sources = load_corpus(documents_dir, chunker)
    validate_relevant_sources(cases, sources)

    calibration_items = build_calibration_dataset(
        old_report_path=old_report_path,
        old_human_reviews_path=old_human_path,
        v012_dir=v012_dir,
    )

    out.mkdir(parents=True, exist_ok=False)
    inputs_dir = out / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(questions_path, inputs_dir / "questions.reviewed.jsonl")

    calibration_path = out / "calibration_cases.jsonl"
    with calibration_path.open("x", encoding="utf-8") as stream:
        for item in calibration_items:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")

    index_dir = out / "index"
    index_dir.mkdir(parents=True, exist_ok=False)
    seed_cache = args.seed_vector_cache.resolve() if args.seed_vector_cache else None
    seeded_vector_cache = False
    if seed_cache and seed_cache.is_file():
        shutil.copy2(seed_cache, index_dir / "vectors.sqlite3")
        seeded_vector_cache = True

    repo = JsonChunkRepository(index_dir)
    repo.save(chunks)

    # Optionally warm/verify the local E5 index offline during prepare so execute only encodes queries.
    e5_prepare_stats: dict[str, object] = {"seeded_from_existing_cache": seeded_vector_cache}
    if not getattr(args, "skip_e5_prewarm", False):
        started = perf_counter()
        settings = create_rag_settings(index_dir, args.model_dir.resolve())
        cached_embedding = create_cached_embedding(settings)
        dense = DenseRetriever(cached_embedding)
        dense.index(chunks)
        e5_prepare_stats.update(
            prewarmed=True,
            elapsed_ms=round((perf_counter() - started) * 1000, 3),
            cache_stats=dict(cached_embedding.last_stats),
            model_descriptor=getattr(cached_embedding.model, "descriptor", {}),
        )
    else:
        e5_prepare_stats["prewarmed"] = False

    corpus_files_sha = {
        p.relative_to(documents_dir).as_posix(): sha256_path(p)
        for p in sorted(documents_dir.rglob("*"))
        if p.is_file()
    }
    manifest_path = documents_dir / "manifest.json"
    manifest_sha = sha256_path(manifest_path) if manifest_path.is_file() else None

    answerable_count = sum(c.expected_status == "answered" for c in cases)
    clarification_count = sum(c.expected_status == "needs_clarification" for c in cases)
    refusal_count = sum(c.expected_status == "insufficient_evidence" for c in cases)

    protocol = {
        "schema_version": "1.0",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": out.name,
        "split": "development",
        "held_out_test_run": False,
        "questions": {
            "source_path": questions_path.relative_to(ROOT).as_posix()
            if questions_path.is_relative_to(ROOT)
            else questions_path.as_posix(),
            "frozen_copy": "inputs/questions.reviewed.jsonl",
            "sha256": sha256_path(inputs_dir / "questions.reviewed.jsonl"),
            "revision1_questions_path": REVISION1_QUESTIONS.relative_to(ROOT).as_posix()
            if REVISION1_QUESTIONS.is_file()
            else None,
            "revision1_questions_sha256": sha256_path(REVISION1_QUESTIONS)
            if REVISION1_QUESTIONS.is_file()
            else None,
            "total_count": len(cases),
            "answerable_count": answerable_count,
            "needs_clarification_count": clarification_count,
            "insufficient_evidence_count": refusal_count,
            "case_ids": [c.case_id for c in cases],
        },
        "corpus": {
            "directory": documents_dir.relative_to(ROOT).as_posix()
            if documents_dir.is_relative_to(ROOT)
            else documents_dir.as_posix(),
            "document_count": len(sources),
            "chunk_count": len(chunks),
            "manifest_sha256": manifest_sha,
            "files_sha256": corpus_files_sha,
            "chunks_sha256": hashlib.sha256(
                json.dumps(
                    [c.to_dict() for c in chunks], sort_keys=True, ensure_ascii=False
                ).encode("utf-8")
            ).hexdigest(),
        },
        "rag_configuration": {
            "system_variant": "best_non_rerank_e5_hybrid_large_corpus",
            "chunking_strategy": "sections",
            "chunk_size": 500,
            "chunk_overlap": 80,
            "embedding_provider": "local_e5",
            "embedding_model": E5_MODEL,
            "embedding_revision": E5_REVISION,
            "embedding_device": "cpu",
            "embedding_cpu_threads": 4,
            "embedding_batch_size": 8,
            "embedding_max_length": 512,
            "retrieval_strategy": "hybrid",
            "rrf_k": 60,
            "rrf_candidate_k": 50,
            "top_k": 5,
            "reranker_provider": "none",
            "context_policy": "neighbors",
            "context_char_budget": 8000,
            "llm_provider": "deepseek",
            "preferred_llm_model": "deepseek-flash",
            "llm_base_url": "https://api.deepseek.com",
            "llm_temperature": 0.1,
            "llm_max_tokens": 1600,
            "llm_verify_support": True,
            "sdk_retries": 0,
            "generator_system_prompt": SYSTEM_PROMPT,
            "e5_prepare_stats": e5_prepare_stats,
        },
        "judge_configuration": {
            "provider": "deepseek",
            "preferred_model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
            "temperature": 0.1,
            "max_tokens": 1600,
            "sdk_retries": 0,
            "max_parse_retries": 1,
            "rubric_version": JUDGE_RUBRIC_VERSION,
            "system_prompt": JUDGE_SYSTEM_PROMPT,
            "verdict_json_schema": JudgeVerdict.model_json_schema(),
            "self_judge_caveat": (
                "Generator and Judge both use DeepSeek (same model family); "
                "calibrated against 11 human (JQH) and 12 assistant (Codex) labels."
            ),
        },
        "calibration_sets": {
            "calibration_cases_file": "calibration_cases.jsonl",
            "calibration_cases_sha256": sha256_path(calibration_path),
            "human_total_count": 11,
            "human_old_v010_count": 6,
            "human_new_v012_count": 5,
            "assistant_v012_count": 12,
            "synthetic_negative_control_count": 3,
            "source_files_sha256": {
                "old_report": sha256_path(old_report_path),
                "old_human_reviews": sha256_path(old_human_path),
                "v012_bm25_report": sha256_path(v012_dir / "bm25.report.json"),
                "v012_e5_hybrid_report": sha256_path(v012_dir / "e5_hybrid.report.json"),
                "v012_blind_pending": sha256_path(v012_dir / "answers.blind.pending.jsonl"),
                "v012_assistant_reviews": sha256_path(
                    v012_dir / "assistant-review-20261028T071527Z" / "assistant-reviews.jsonl"
                ),
            },
        },
        "budget": {
            "hard_total_tokens": 800_000,
            "max_completion_calls": 220,
            "unknown_usage_stops_next_call": True,
        },
        "pipeline_code_sha256": pipeline_code_fingerprint(),
        "pipeline_code_files_sha256": pipeline_code_hashes(),
    }
    write_json_exclusive(out / "protocol.json", protocol)
    protocol_sha = sha256_path(out / "protocol.json")
    result = {
        "prepared_dir": str(out),
        "protocol_sha256": protocol_sha,
        "questions_sha256": protocol["questions"]["sha256"],
        "question_counts": {
            "total": len(cases),
            "answerable": answerable_count,
            "needs_clarification": clarification_count,
            "insufficient_evidence": refusal_count,
        },
        "corpus": {
            "document_count": len(sources),
            "chunk_count": len(chunks),
        },
        "calibration_counts": {
            "human": 11,
            "assistant": 12,
            "synthetic_negative_control": 3,
        },
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def verify_frozen_protocol(out: Path) -> dict[str, object]:
    protocol_path = out / "protocol.json"
    if not protocol_path.is_file():
        raise ValueError(f"未找到冻结协议文件：{protocol_path}")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol.get("split") != "development" or protocol.get("held_out_test_run") is not False:
        raise ValueError("协议划分不合法")
    if protocol.get("pipeline_code_sha256") != pipeline_code_fingerprint():
        raise ValueError("冻结后的评测链路源码已改变，请重新运行 prepare 生成新协议")
    frozen_questions = out / protocol["questions"]["frozen_copy"]
    if sha256_path(frozen_questions) != protocol["questions"]["sha256"]:
        raise ValueError("冻结的问题文件哈希不匹配")
    calibration_path = out / protocol["calibration_sets"]["calibration_cases_file"]
    if sha256_path(calibration_path) != protocol["calibration_sets"]["calibration_cases_sha256"]:
        raise ValueError("冻结的校准集文件哈希不匹配")
    documents_dir = ROOT / protocol["corpus"]["directory"]
    if not documents_dir.is_dir():
        documents_dir = Path(protocol["corpus"]["directory"])
    actual_corpus = {
        p.relative_to(documents_dir).as_posix(): sha256_path(p)
        for p in sorted(documents_dir.rglob("*"))
        if p.is_file()
    }
    if actual_corpus != protocol["corpus"]["files_sha256"]:
        raise ValueError("冻结的语料文件已改变")
    return protocol


def _create_live_generator_and_judge(
    out: Path,
    protocol: dict[str, object],
    budget: TokenBudgetTracker,
    *,
    generator_factory=None,
) -> tuple[BoundedDeepSeekGenerator, DeepSeekJudge]:
    if generator_factory is not None:
        generator = generator_factory(budget)
    else:
        generator = BoundedDeepSeekGenerator.from_user_env(
            budget=budget,
            verify_support=bool(protocol["rag_configuration"]["llm_verify_support"]),
        )
    judge = DeepSeekJudge(
        generator,
        max_parse_retries=int(protocol["judge_configuration"]["max_parse_retries"]),
    )
    return generator, judge


def execute(
    args: argparse.Namespace,
    *,
    service_factory=None,
    generator_factory=None,
) -> dict[str, object]:
    out = args.output.resolve()
    protocol = verify_frozen_protocol(out)
    execution_jsonl = out / "execution.jsonl"
    if execution_jsonl.exists():
        raise ValueError(f"执行记录已存在，拒绝覆盖：{execution_jsonl}")
    if getattr(args, "with_calibrate", False) and (out / "calibration.jsonl").exists():
        raise ValueError(f"校准记录已存在，拒绝覆盖：{out / 'calibration.jsonl'}")

    cases = load_reviewed_development_cases(out / protocol["questions"]["frozen_copy"])
    budget_cfg = protocol["budget"]
    budget = TokenBudgetTracker(
        hard_token_limit=int(budget_cfg["hard_total_tokens"]),
        max_completion_calls=int(budget_cfg["max_completion_calls"]),
        journal_path=out / "provider-responses.jsonl",
    )

    wall_started = perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    write_json_exclusive(
        out / "execution.started.json",
        {
            "started_at": started_at,
            "protocol_sha256": sha256_path(out / "protocol.json"),
            "with_calibrate": bool(getattr(args, "with_calibrate", False)),
        },
    )

    if service_factory is not None:
        service = service_factory(out, protocol)
    else:
        settings = create_rag_settings(
            out / "index",
            getattr(args, "model_dir", E5_DIRECTORY).resolve(),
        )
        repo = JsonChunkRepository(settings.data_dir)
        retriever = HybridRetriever(
            DenseRetriever(create_cached_embedding(settings)),
            BM25Retriever(),
        )
        service = RAGService(
            chunker=TextChunker(500, 80, strategy="sections"),
            retriever=retriever,
            generator=ExtractiveGenerator(),
            repository=repo,
            retrieval_strategy="hybrid",
            context_char_budget=8000,
            context_policy="neighbors",
            reranker=None,
        )

    generator, judge = _create_live_generator_and_judge(
        out,
        protocol,
        budget,
        generator_factory=generator_factory,
    )
    service.generator = generator

    write_json_overwrite(
        out / "runtime.json",
        {
            "protocol_sha256": sha256_path(out / "protocol.json"),
            "requested_model": generator.model,
            "verify_support": generator.verify_support,
            "chunk_count": len(service.retriever.dense.chunks),
            "embedding_descriptor": getattr(
                getattr(service.retriever.dense.embedding_model, "model", None),
                "descriptor",
                {},
            ),
            "sdk_retries": 0,
        },
    )

    executed_count = 0
    calibration_count = 0
    try:
        with execution_jsonl.open("x", encoding="utf-8") as stream:
            for index, case in enumerate(cases, start=1):
                if budget.stopped or budget.unknown_usage:
                    break
                generator.phase = "execute_rag"
                generator.job = f"rag/{case.case_id}"
                generator.last_draft = None

                rag_started = perf_counter()
                response = service.answer(case.question, 5, filters=case.filters)
                rag_elapsed_ms = round((perf_counter() - rag_started) * 1000, 3)

                context_hits = hits_from_response(response)
                context_recall = (
                    sum(evidence_covered(anchor, context_hits) for anchor in case.evidence)
                    / len(case.evidence)
                    if case.evidence
                    else None
                )

                pre_draft: dict[str, object] | None = None
                if generator.last_draft is not None:
                    pre_draft = {
                        "status": generator.last_draft.status,
                        "answer": generator.last_draft.answer,
                        "reason": generator.last_draft.reason,
                        "claims": generator.last_draft.claims,
                        "citation_ids": generator.last_draft.citation_ids,
                    }

                judge_payload = build_judge_payload(
                    case_id=case.case_id,
                    question=case.question,
                    filters=case.filters,
                    expected_status=case.expected_status,
                    reference_answer=case.reference_answer,
                    gold_evidence=list(case.evidence),
                    response=response,
                )
                judge_started = perf_counter()
                judge_result = judge.evaluate(
                    phase="execute",
                    job=f"judge/{case.case_id}",
                    payload=judge_payload,
                )
                judge_elapsed_ms = round((perf_counter() - judge_started) * 1000, 3)

                # If support verification blocked an answered draft, also judge the pre-verification draft
                # so the report can transparently compare verified vs unverified draft quality.
                draft_judge_result: dict[str, object] | None = None
                if (
                    pre_draft is not None
                    and pre_draft["status"] == "answered"
                    and response.get("status") != "answered"
                    and not (budget.stopped or budget.unknown_usage)
                ):
                    draft_response = {
                        **response,
                        **answer_fields(generator.last_draft, context_hits),
                    }
                    draft_payload = build_judge_payload(
                        case_id=case.case_id,
                        question=case.question,
                        filters=case.filters,
                        expected_status=case.expected_status,
                        reference_answer=case.reference_answer,
                        gold_evidence=list(case.evidence),
                        response=draft_response,
                    )
                    draft_judge_result = judge.evaluate(
                        phase="execute_draft",
                        job=f"judge_draft/{case.case_id}",
                        payload=draft_payload,
                    )

                row = {
                    "index": index,
                    "id": case.case_id,
                    "category": case.category,
                    "question": case.question,
                    "filters": case.filters,
                    "should_answer": case.should_answer,
                    "expected_status": case.expected_status,
                    "reference_answer": case.reference_answer,
                    "gold_evidence": list(case.evidence),
                    "context_evidence_recall": context_recall,
                    "rag_elapsed_ms": rag_elapsed_ms,
                    "judge_elapsed_ms": judge_elapsed_ms,
                    "response": response,
                    "pre_verification_draft": pre_draft,
                    "judge": judge_result,
                    "draft_judge": draft_judge_result,
                }
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                executed_count += 1
                verdict_label = (
                    judge_result["verdict"]["correctness"]
                    if judge_result.get("verdict")
                    else judge_result["status"]
                )
                print(
                    f"[{index:02d}/{len(cases)}] {case.case_id}: rag={response['status']} "
                    f"judge={verdict_label} calls={budget.calls} tokens={budget.total_tokens}",
                    flush=True,
                )

        if getattr(args, "with_calibrate", False) and not (budget.stopped or budget.unknown_usage):
            calibration_count = _run_calibration_loop(out, protocol, judge, budget)
    finally:
        if hasattr(generator, "client") and hasattr(generator.client, "close"):
            generator.client.close()
        wall_seconds = round(perf_counter() - wall_started, 3)
        finished_summary = {
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "wall_seconds": wall_seconds,
            "executed_question_count": executed_count,
            "planned_question_count": len(cases),
            "calibrated_item_count": calibration_count,
            "budget": budget.summary(),
        }
        write_json_overwrite(out / "execution.finished.json", finished_summary)
    return finished_summary


def _run_calibration_loop(
    out: Path,
    protocol: dict[str, object],
    judge: DeepSeekJudge,
    budget: TokenBudgetTracker,
) -> int:
    calibration_cases = read_jsonl(out / protocol["calibration_sets"]["calibration_cases_file"])
    calibration_out = out / "calibration.jsonl"
    count = 0
    with calibration_out.open("x", encoding="utf-8") as stream:
        for index, item in enumerate(calibration_cases, start=1):
            if budget.stopped or budget.unknown_usage:
                break
            cal_id = str(item["calibration_id"])
            payload = build_judge_payload(
                case_id=str(item["case_id"]),
                question=str(item["question"]),
                filters=dict(item.get("filters", {})),
                expected_status=str(item.get("expected_status", "answered")),
                reference_answer=str(item["reference_answer"]),
                gold_evidence=list(item.get("gold_evidence", [])),
                response=dict(item["response"]),
            )
            started = perf_counter()
            judge_result = judge.evaluate(
                phase="calibrate",
                job=f"calibrate/{cal_id}",
                payload=payload,
            )
            elapsed_ms = round((perf_counter() - started) * 1000, 3)
            record = {
                "index": index,
                "calibration_id": cal_id,
                "group": item["group"],
                "subgroup": item["subgroup"],
                "case_id": item["case_id"],
                "arm": item["arm"],
                "reviewer_kind": item["reviewer_kind"],
                "reviewed_by": item["reviewed_by"],
                "reviewed_at": item["reviewed_at"],
                "review_status": item["review_status"],
                "report_sha256": item["report_sha256"],
                "draft_sha256": item["draft_sha256"],
                "label_answer_correct": item["label_answer_correct"],
                "label_all_claims_supported": item["label_all_claims_supported"],
                "label_notes": item.get("label_notes", ""),
                "question": item["question"],
                "reference_answer": item["reference_answer"],
                "answer": item["response"].get("answer", ""),
                "judge_elapsed_ms": elapsed_ms,
                "judge": judge_result,
            }
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            count += 1
            verdict_label = (
                judge_result["verdict"]["correctness"]
                if judge_result.get("verdict")
                else judge_result["status"]
            )
            print(
                f"[cal {index:02d}/{len(calibration_cases)}] {cal_id} ({item['group']}): "
                f"judge={verdict_label} calls={budget.calls} tokens={budget.total_tokens}",
                flush=True,
            )
    return count


def calibrate(
    args: argparse.Namespace,
    *,
    generator_factory=None,
) -> dict[str, object]:
    out = args.output.resolve()
    protocol = verify_frozen_protocol(out)
    calibration_out = out / "calibration.jsonl"
    if calibration_out.exists():
        raise ValueError(f"校准结果已存在，拒绝覆盖：{calibration_out}")

    budget_cfg = protocol["budget"]
    existing_calls = 0
    existing_prompt = 0
    existing_completion = 0
    existing_total = 0
    journal_path = out / "provider-responses.jsonl"
    if journal_path.is_file():
        for entry in read_jsonl(journal_path):
            existing_calls += 1
            comp = entry.get("completion")
            if isinstance(comp, dict):
                existing_prompt += int(comp.get("prompt_tokens") or 0)
                existing_completion += int(comp.get("completion_tokens") or 0)
                existing_total += int(comp.get("total_tokens") or 0)

    budget = TokenBudgetTracker(
        hard_token_limit=int(budget_cfg["hard_total_tokens"]),
        max_completion_calls=int(budget_cfg["max_completion_calls"]),
        journal_path=journal_path,
        calls=existing_calls,
        prompt_tokens=existing_prompt,
        completion_tokens=existing_completion,
        total_tokens=existing_total,
    )
    wall_started = perf_counter()
    generator, judge = _create_live_generator_and_judge(
        out,
        protocol,
        budget,
        generator_factory=generator_factory,
    )
    try:
        count = _run_calibration_loop(out, protocol, judge, budget)
    finally:
        if hasattr(generator, "client") and hasattr(generator.client, "close"):
            generator.client.close()
    summary = {
        "calibrated_item_count": count,
        "wall_seconds": round(perf_counter() - wall_started, 3),
        "budget": budget.summary(),
    }
    write_json_overwrite(out / "calibration.finished.json", summary)
    return summary


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 4) if denominator else None,
    }


def build_summary_report(out: Path) -> dict[str, object]:
    protocol = json.loads((out / "protocol.json").read_text(encoding="utf-8"))
    protocol_sha = sha256_path(out / "protocol.json")
    runtime = (
        json.loads((out / "runtime.json").read_text(encoding="utf-8"))
        if (out / "runtime.json").is_file()
        else {}
    )
    exec_finished = (
        json.loads((out / "execution.finished.json").read_text(encoding="utf-8"))
        if (out / "execution.finished.json").is_file()
        else {}
    )
    execution_rows = read_jsonl(out / "execution.jsonl")
    calibration_rows = (
        read_jsonl(out / "calibration.jsonl") if (out / "calibration.jsonl").is_file() else []
    )
    journal_rows = (
        read_jsonl(out / "provider-responses.jsonl")
        if (out / "provider-responses.jsonl").is_file()
        else []
    )

    answerable_rows = [r for r in execution_rows if r["expected_status"] == "answered"]
    non_answerable_rows = [r for r in execution_rows if r["expected_status"] != "answered"]
    clarification_rows = [
        r for r in execution_rows if r["expected_status"] == "needs_clarification"
    ]
    refusal_rows = [r for r in execution_rows if r["expected_status"] == "insufficient_evidence"]

    # End-to-end answerable metrics (out of 41)
    ans_correct = sum(
        r["judge"].get("verdict", {}).get("correctness") == "correct"
        for r in answerable_rows
        if r["judge"].get("verdict")
    )
    ans_partial = sum(
        r["judge"].get("verdict", {}).get("correctness") == "partially_correct"
        for r in answerable_rows
        if r["judge"].get("verdict")
    )
    ans_incorrect = sum(
        r["judge"].get("verdict", {}).get("correctness") == "incorrect"
        for r in answerable_rows
        if r["judge"].get("verdict")
    )
    ans_core_answered = sum(
        bool(r["judge"].get("verdict", {}).get("core_question_answered"))
        for r in answerable_rows
        if r["judge"].get("verdict")
    )
    ans_faithful_all = sum(
        bool(r["judge"].get("verdict", {}).get("faithfulness"))
        for r in answerable_rows
        if r["judge"].get("verdict")
    )
    answered_subset = [r for r in answerable_rows if r["response"].get("status") == "answered"]
    ans_faithful_on_answered = sum(
        bool(r["judge"].get("verdict", {}).get("faithfulness"))
        for r in answered_subset
        if r["judge"].get("verdict")
    )
    unsupported_claim_cases = [
        {
            "id": r["id"],
            "unsupported_claims": r["judge"]["verdict"]["unsupported_claims"],
        }
        for r in execution_rows
        if r["judge"].get("verdict") and r["judge"]["verdict"].get("unsupported_claims")
    ]

    # Pre-verification draft comparison on answerable questions
    blocked_by_verifier = [
        r
        for r in answerable_rows
        if (r.get("pre_verification_draft") or {}).get("status") == "answered"
        and r["response"].get("reason") == "support_verification_failed"
    ]
    draft_correct = 0
    draft_partial = 0
    draft_incorrect = 0
    for r in answerable_rows:
        effective_judge = r.get("draft_judge") or r["judge"]
        verdict = effective_judge.get("verdict")
        if verdict:
            c_val = verdict.get("correctness")
            if c_val == "correct":
                draft_correct += 1
            elif c_val == "partially_correct":
                draft_partial += 1
            else:
                draft_incorrect += 1

    # Clarification / refusal metrics (out of 6)
    non_ans_behavior_matched = sum(
        bool(r["judge"].get("verdict", {}).get("behavior_matched"))
        and r["response"].get("status") == r["expected_status"]
        for r in non_answerable_rows
        if r["judge"].get("verdict")
    )
    non_ans_correct = sum(
        r["judge"].get("verdict", {}).get("correctness") == "correct"
        for r in non_answerable_rows
        if r["judge"].get("verdict")
    )
    non_ans_partial = sum(
        r["judge"].get("verdict", {}).get("correctness") == "partially_correct"
        for r in non_answerable_rows
        if r["judge"].get("verdict")
    )
    non_ans_incorrect = sum(
        r["judge"].get("verdict", {}).get("correctness") == "incorrect"
        for r in non_answerable_rows
        if r["judge"].get("verdict")
    )
    non_ans_faithful = sum(
        bool(r["judge"].get("verdict", {}).get("faithfulness"))
        for r in non_answerable_rows
        if r["judge"].get("verdict")
    )

    clar_correct = sum(
        r["response"].get("status") == "needs_clarification"
        and r["judge"].get("verdict", {}).get("correctness") == "correct"
        for r in clarification_rows
        if r["judge"].get("verdict")
    )
    refusal_correct = sum(
        r["response"].get("status") == "insufficient_evidence"
        and r["judge"].get("verdict", {}).get("correctness") == "correct"
        for r in refusal_rows
        if r["judge"].get("verdict")
    )

    # Context evidence coverage on the 41 answerable questions
    complete_evidence_count = sum(
        (r.get("context_evidence_recall") or 0.0) >= 1.0 for r in answerable_rows
    )
    missing_evidence_ids = [
        r["id"] for r in answerable_rows if (r.get("context_evidence_recall") or 0.0) < 1.0
    ]

    # Non-correct answerable breakdown
    non_correct_answerable_cases = [
        {
            "id": r["id"],
            "question": r["question"],
            "filters": r["filters"],
            "context_evidence_recall": r["context_evidence_recall"],
            "system_status": r["response"].get("status"),
            "system_reason": r["response"].get("reason", ""),
            "pre_verification_draft_status": (r.get("pre_verification_draft") or {}).get("status"),
            "judge_correctness": r["judge"]["verdict"]["correctness"]
            if r["judge"].get("verdict")
            else None,
            "judge_faithfulness": r["judge"]["verdict"]["faithfulness"]
            if r["judge"].get("verdict")
            else None,
            "judge_reasoning": r["judge"]["verdict"]["reasoning"]
            if r["judge"].get("verdict")
            else None,
            "draft_judge_correctness": (r.get("draft_judge") or {})
            .get("verdict", {})
            .get("correctness")
            if r.get("draft_judge")
            else None,
        }
        for r in answerable_rows
        if not r["judge"].get("verdict") or r["judge"]["verdict"]["correctness"] != "correct"
    ]

    # Calibration summaries (kept strictly separate: human vs assistant vs synthetic negative controls)
    human_all_rows = [r for r in calibration_rows if r["group"] == "human"]
    human_old_rows = [r for r in calibration_rows if r["subgroup"] == "human_old_v010"]
    human_new_rows = [r for r in calibration_rows if r["subgroup"] == "human_new_v012"]
    assistant_rows = [r for r in calibration_rows if r["group"] == "assistant"]
    synthetic_rows = [r for r in calibration_rows if r["group"] == "synthetic_negative_control"]

    # Usage & calls summary from provider-responses.jsonl
    by_phase: dict[str, dict[str, int | float]] = {}
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    for call in journal_rows:
        phase = str(call.get("phase", "unknown"))
        bucket = by_phase.setdefault(
            phase,
            {
                "calls": 0,
                "ok_calls": 0,
                "error_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "elapsed_ms_total": 0.0,
            },
        )
        bucket["calls"] = int(bucket["calls"]) + 1
        if call.get("status") == "ok":
            bucket["ok_calls"] = int(bucket["ok_calls"]) + 1
        else:
            bucket["error_calls"] = int(bucket["error_calls"]) + 1
        comp = call.get("completion")
        if isinstance(comp, dict):
            pt = int(comp.get("prompt_tokens") or 0)
            ct = int(comp.get("completion_tokens") or 0)
            tt = int(comp.get("total_tokens") or 0)
            bucket["prompt_tokens"] = int(bucket["prompt_tokens"]) + pt
            bucket["completion_tokens"] = int(bucket["completion_tokens"]) + ct
            bucket["total_tokens"] = int(bucket["total_tokens"]) + tt
            total_prompt_tokens += pt
            total_completion_tokens += ct
            total_tokens += tt
        bucket["elapsed_ms_total"] = round(
            float(bucket["elapsed_ms_total"]) + float(call.get("elapsed_ms") or 0.0), 3
        )

    exec_parse_failures = sum(int(r["judge"].get("parse_failure_count", 0)) for r in execution_rows)
    exec_retries = sum(int(r["judge"].get("retry_count", 0)) for r in execution_rows)
    cal_parse_failures = sum(
        int(r["judge"].get("parse_failure_count", 0)) for r in calibration_rows
    )
    cal_retries = sum(int(r["judge"].get("retry_count", 0)) for r in calibration_rows)

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": protocol["experiment_id"],
        "split": "development",
        "held_out_test_run": False,
        "provenance": {
            "protocol_sha256": protocol_sha,
            "questions_path": protocol["questions"]["source_path"],
            "questions_sha256": protocol["questions"]["sha256"],
            "revision1_questions_path": protocol["questions"]["revision1_questions_path"],
            "revision1_questions_sha256": protocol["questions"]["revision1_questions_sha256"],
            "corpus_dir": protocol["corpus"]["directory"],
            "corpus_manifest_sha256": protocol["corpus"]["manifest_sha256"],
            "chunks_sha256": protocol["corpus"]["chunks_sha256"],
            "pipeline_code_sha256": protocol["pipeline_code_sha256"],
            "execution_jsonl_sha256": sha256_path(out / "execution.jsonl"),
            "calibration_jsonl_sha256": sha256_path(out / "calibration.jsonl")
            if (out / "calibration.jsonl").is_file()
            else None,
            "provider_responses_jsonl_sha256": sha256_path(out / "provider-responses.jsonl")
            if (out / "provider-responses.jsonl").is_file()
            else None,
        },
        "configuration": {
            "rag": {
                k: v
                for k, v in protocol["rag_configuration"].items()
                if k not in {"generator_system_prompt", "e5_prepare_stats"}
            },
            "judge": {
                "provider": protocol["judge_configuration"]["provider"],
                "model": runtime.get(
                    "requested_model", protocol["judge_configuration"]["preferred_model"]
                ),
                "rubric_version": protocol["judge_configuration"]["rubric_version"],
                "max_parse_retries": protocol["judge_configuration"]["max_parse_retries"],
            },
            "dataset": {
                "document_count": protocol["corpus"]["document_count"],
                "chunk_count": protocol["corpus"]["chunk_count"],
                "question_count": protocol["questions"]["total_count"],
                "answerable_count": protocol["questions"]["answerable_count"],
                "needs_clarification_count": protocol["questions"]["needs_clarification_count"],
                "insufficient_evidence_count": protocol["questions"]["insufficient_evidence_count"],
            },
        },
        "end_to_end_evaluation": {
            "total_questions": len(execution_rows),
            "overall_correct": _rate(ans_correct + non_ans_correct, len(execution_rows)),
            "overall_faithful": _rate(ans_faithful_all + non_ans_faithful, len(execution_rows)),
            "overall_behavior_matched": _rate(
                sum(r["response"].get("status") == r["expected_status"] for r in execution_rows),
                len(execution_rows),
            ),
            "answerable_questions": {
                "count": len(answerable_rows),
                "context_complete_evidence_coverage": _rate(
                    complete_evidence_count, len(answerable_rows)
                ),
                "missing_complete_evidence_case_ids": missing_evidence_ids,
                "rag_status_counts": dict(
                    Counter(str(r["response"].get("status")) for r in answerable_rows)
                ),
                "rag_reason_counts": dict(
                    Counter(str(r["response"].get("reason") or "none") for r in answerable_rows)
                ),
                "correctness_counts": {
                    "correct": ans_correct,
                    "partially_correct": ans_partial,
                    "incorrect": ans_incorrect,
                },
                "correct_rate": _rate(ans_correct, len(answerable_rows)),
                "correct_or_partial_rate": _rate(ans_correct + ans_partial, len(answerable_rows)),
                "core_question_answered_rate": _rate(ans_core_answered, len(answerable_rows)),
                "faithfulness_on_all_answerable": _rate(ans_faithful_all, len(answerable_rows)),
                "faithfulness_on_answered_responses": _rate(
                    ans_faithful_on_answered, len(answered_subset)
                ),
                "unsupported_claim_cases": unsupported_claim_cases,
                "pre_verification_draft_comparison": {
                    "drafts_answered_count": sum(
                        (r.get("pre_verification_draft") or {}).get("status") == "answered"
                        for r in answerable_rows
                    ),
                    "blocked_by_support_verification_count": len(blocked_by_verifier),
                    "blocked_case_ids": [r["id"] for r in blocked_by_verifier],
                    "draft_correctness_counts": {
                        "correct": draft_correct,
                        "partially_correct": draft_partial,
                        "incorrect": draft_incorrect,
                    },
                    "draft_correct_rate": _rate(draft_correct, len(answerable_rows)),
                },
                "non_correct_cases": non_correct_answerable_cases,
            },
            "clarification_and_refusal_questions": {
                "count": len(non_answerable_rows),
                "behavior_matched_rate": _rate(non_ans_behavior_matched, len(non_answerable_rows)),
                "correctness_counts": {
                    "correct": non_ans_correct,
                    "partially_correct": non_ans_partial,
                    "incorrect": non_ans_incorrect,
                },
                "correct_rate": _rate(non_ans_correct, len(non_answerable_rows)),
                "faithfulness_rate": _rate(non_ans_faithful, len(non_answerable_rows)),
                "needs_clarification_breakdown": {
                    "count": len(clarification_rows),
                    "correct_count": clar_correct,
                    "case_ids": [r["id"] for r in clarification_rows],
                },
                "insufficient_evidence_breakdown": {
                    "count": len(refusal_rows),
                    "correct_count": refusal_correct,
                    "case_ids": [r["id"] for r in refusal_rows],
                },
            },
            "judge_reliability": {
                "parse_failure_count": exec_parse_failures,
                "documented_retry_count": exec_retries,
                "unrecovered_parse_error_count": sum(
                    r["judge"].get("status") == "parse_error" for r in execution_rows
                ),
            },
        },
        "judge_calibration": {
            "note": (
                "Human (JQH) labels and AI assistant (Codex) labels are kept strictly separate. "
                "Synthetic contradiction negative controls are also reported separately."
            ),
            "vs_human_all": summarize_calibration_group(human_all_rows),
            "vs_human_old_v010": summarize_calibration_group(human_old_rows),
            "vs_human_new_v012": summarize_calibration_group(human_new_rows),
            "vs_assistant_v012": summarize_calibration_group(assistant_rows),
            "vs_synthetic_negative_controls": summarize_calibration_group(synthetic_rows),
            "judge_reliability": {
                "parse_failure_count": cal_parse_failures,
                "documented_retry_count": cal_retries,
                "unrecovered_parse_error_count": sum(
                    r["judge"].get("status") == "parse_error" for r in calibration_rows
                ),
            },
        },
        "usage_and_runtime": {
            "hard_token_budget": protocol["budget"]["hard_total_tokens"],
            "max_completion_calls": protocol["budget"]["max_completion_calls"],
            "total_sdk_calls": len(journal_rows),
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "execute_wall_seconds": exec_finished.get("wall_seconds"),
            "by_phase": by_phase,
        },
        "caveats": [
            "Self-judge bias: both the RAG generator/verifier and the LLM-as-Judge use DeepSeek (deepseek-flash), which can share blind spots or stylistic preferences.",
            "Small development set: results are measured on the 47 human-reviewed development questions (41 answerable + 6 clarification/refusal), not the 8 held-out test questions.",
            "Calibration sample size: human calibration has n=11 (all positive natural drafts from JQH) and assistant calibration has n=12 (11 positive + 1 source-conflict negative R015 from Codex), supplemented separately by n=3 synthetic contradiction controls.",
            "Chinese documentation translation lag: as seen in R015/support-011 (issue F001), faithful reproduction of an outdated Chinese doc sentence can still be factually outdated for the target FastAPI/Starlette version.",
        ],
    }


def render_summary_markdown(summary: dict[str, object]) -> str:
    prov = summary["provenance"]
    cfg = summary["configuration"]
    e2e = summary["end_to_end_evaluation"]
    ans = e2e["answerable_questions"]
    non_ans = e2e["clarification_and_refusal_questions"]
    cal = summary["judge_calibration"]
    usage = summary["usage_and_runtime"]

    h_all = cal["vs_human_all"]
    a_all = cal["vs_assistant_v012"]
    s_all = cal["vs_synthetic_negative_controls"]

    lines = [
        "# 已审核开发集端到端答案评测与 LLM-as-Judge 校准报告（2026-11-02）",
        "",
        f"- 协议 SHA256：`{prov['protocol_sha256']}`",
        f"- 问题文件：`{prov['questions_path']}`（SHA256：`{prov['questions_sha256']}`，共 {cfg['dataset']['question_count']} 题：可回答 {cfg['dataset']['answerable_count']} 题、需澄清 {cfg['dataset']['needs_clarification_count']} 题、证据不足拒答 {cfg['dataset']['insufficient_evidence_count']} 题；保留测试集 8 题未运行）",
        f"- 语料规模：`{prov['corpus_dir']}`（{cfg['dataset']['document_count']} 份文档、{cfg['dataset']['chunk_count']} 个 `sections 500/80` 分块）",
        f"- 检索与生成配置：`local_e5`（`{cfg['rag']['embedding_model']}@{cfg['rag']['embedding_revision']}`）+ BM25 RRF Hybrid、Top-{cfg['rag']['top_k']}、`{cfg['rag']['context_policy']}`（预算 {cfg['rag']['context_char_budget']} 字符）、重排 `none`、生成与核验模型 `{cfg['judge']['model']}`（默认开启 `verify_support=True`）",
        f"- Judge 配置：独立 DeepSeek 调用（`{cfg['judge']['model']}`，Rubric `{cfg['judge']['rubric_version']}`，Pydantic 严格 JSON 校验，最多 {cfg['judge']['max_parse_retries']} 次有记录重试）",
        "",
        "## 1. 端到端问答评测结果（47 道已审核开发题）",
        "",
        "### 1.1 可回答问题（41 题）",
        "",
        "| 指标 | 计数 / 分母 | 比例 | 说明 |",
        "|---|---:|---:|---|",
        f"| 检索上下文完整证据覆盖 | {ans['context_complete_evidence_coverage']['numerator']} / {ans['context_complete_evidence_coverage']['denominator']} | {ans['context_complete_evidence_coverage']['rate']:.4f} | 缺完整证据锚点题目：{', '.join(ans['missing_complete_evidence_case_ids']) or '无'} |",
        f"| 端到端正确（`correct`） | {ans['correctness_counts']['correct']} / {ans['count']} | {ans['correct_rate']['rate']:.4f} | 完整覆盖参考答案且无版本事实冲突 |",
        f"| 部分正确（`partially_correct`） | {ans['correctness_counts']['partially_correct']} / {ans['count']} | {ans['correctness_counts']['partially_correct'] / ans['count']:.4f} | 核心结论正确但遗漏次要条件或收紧不足 |",
        f"| 不正确（`incorrect`） | {ans['correctness_counts']['incorrect']} / {ans['count']} | {ans['correctness_counts']['incorrect'] / ans['count']:.4f} | 含检索缺失拒答、核验误拦截或事实冲突 |",
        f"| 核心问题答对（`core_question_answered`） | {ans['core_question_answered_rate']['numerator']} / {ans['core_question_answered_rate']['denominator']} | {ans['core_question_answered_rate']['rate']:.4f} | 直接准确回答问题核心诉求 |",
        f"| 忠实度（全 41 题 `faithfulness`） | {ans['faithfulness_on_all_answerable']['numerator']} / {ans['faithfulness_on_all_answerable']['denominator']} | {ans['faithfulness_on_all_answerable']['rate']:.4f} | 含安全拒答无编造结论 |",
        f"| 忠实度（已实际作答子集 `faithfulness`） | {ans['faithfulness_on_answered_responses']['numerator']} / {ans['faithfulness_on_answered_responses']['denominator']} | {(ans['faithfulness_on_answered_responses']['rate'] or 0.0):.4f} | 实际输出结论均由检索上下文支持 |",
        "",
        f"- 核验前后对比：41 题中初稿生成 `answered` {ans['pre_verification_draft_comparison']['drafts_answered_count']} 题，被 `verify_support` 拦截 {ans['pre_verification_draft_comparison']['blocked_by_support_verification_count']} 题（{', '.join(ans['pre_verification_draft_comparison']['blocked_case_ids']) or '无'}）；若按核验前初稿统计，Judge 判定 `correct` {ans['pre_verification_draft_comparison']['draft_correctness_counts']['correct']}/41、`partially_correct` {ans['pre_verification_draft_comparison']['draft_correctness_counts']['partially_correct']}/41、`incorrect` {ans['pre_verification_draft_comparison']['draft_correctness_counts']['incorrect']}/41。",
        "",
        "### 1.2 澄清与拒答边界题（6 题）",
        "",
        "| 类别 | 题数 | 行为与判定完全正确 (`correct`) | 忠实无编造 (`faithfulness`) | 题目编号 |",
        "|---|---:|---:|---:|---|",
        f"| 缺少版本需澄清（`needs_clarification`） | {non_ans['needs_clarification_breakdown']['count']} | {non_ans['needs_clarification_breakdown']['correct_count']} / {non_ans['needs_clarification_breakdown']['count']} | {non_ans['needs_clarification_breakdown']['correct_count']} / {non_ans['needs_clarification_breakdown']['count']} | {', '.join(non_ans['needs_clarification_breakdown']['case_ids'])} |",
        f"| 未知版本或超范围拒答（`insufficient_evidence`） | {non_ans['insufficient_evidence_breakdown']['count']} | {non_ans['insufficient_evidence_breakdown']['correct_count']} / {non_ans['insufficient_evidence_breakdown']['count']} | {non_ans['insufficient_evidence_breakdown']['correct_count']} / {non_ans['insufficient_evidence_breakdown']['count']} | {', '.join(non_ans['insufficient_evidence_breakdown']['case_ids'])} |",
        f"| **合计** | **{non_ans['count']}** | **{non_ans['correct_rate']['numerator']} / {non_ans['count']} ({non_ans['correct_rate']['rate']:.4f})** | **{non_ans['faithfulness_rate']['numerator']} / {non_ans['count']} ({non_ans['faithfulness_rate']['rate']:.4f})** | 全部 6 题边界行为 |",
        "",
        "### 1.3 未判 `correct` 的可回答题逐项定位",
        "",
    ]

    if ans["non_correct_cases"]:
        lines.extend(
            [
                "| 题目 ID | 上下文证据覆盖 | 系统最终状态 / 原因 | 初稿状态 | Judge 正确性 | Judge 忠实度 | 判定理由 |",
                "|---|---:|---|---|---|---|---|",
            ]
        )
        for item in ans["non_correct_cases"]:
            lines.append(
                f"| `{item['id']}` | {item['context_evidence_recall']:.2f} | "
                f"`{item['system_status']}` (`{item['system_reason'] or 'none'}`) | "
                f"`{item['pre_verification_draft_status'] or 'none'}` | "
                f"`{item['judge_correctness']}` | `{item['judge_faithfulness']}` | "
                f"{str(item['judge_reasoning'] or '').replace('|', '｜')} |"
            )
    else:
        lines.append("- 全部 41 道可回答题均判为 `correct`。")

    lines.extend(
        [
            "",
            "## 2. LLM-as-Judge 校准结果（人工标签与助手标签分开统计）",
            "",
            "| 校准集合 | 样本数 `n` | 正确性严格一致 (`correct` vs `answer_correct`) | 正确性宽松一致 (`correct\\|partial`) | 正确性混淆计数 `(TP, FP, TN, FN)` | 忠实度一致 (`faithfulness` vs `supported`) | 忠实度混淆计数 `(TP, FP, TN, FN)` | 两项同时严格一致 |",
            "|---|---:|---:|---:|---|---:|---|---:|",
        ]
    )

    for label, group in (
        ("真实人工评分合计（JQH：旧6＋新5）", h_all),
        ("└─ 人工旧批（v010 BM25，JQH）", cal["vs_human_old_v010"]),
        ("└─ 人工新批（v012 盲审，JQH）", cal["vs_human_new_v012"]),
        ("AI 助手复核（v012，Codex）", a_all),
        ("合成矛盾负样本（程序化反转，单列）", s_all),
    ):
        cs = group["correctness_strict_agreement"]
        cl = group["correctness_lenient_agreement"]
        fs = group["faithfulness_agreement"]
        js = group["joint_strict_agreement"]
        c_conf = cs["confusion"]
        f_conf = fs["confusion"]
        lines.append(
            f"| {label} | {cs['n']} | "
            f"{cs['agreed']}/{cs['n']} ({(cs['accuracy'] or 0.0):.4f}) | "
            f"{cl['agreed']}/{cl['n']} ({(cl['accuracy'] or 0.0):.4f}) | "
            f"`({c_conf['tp_ref_true_pred_true']}, {c_conf['fp_ref_false_pred_true']}, {c_conf['tn_ref_false_pred_false']}, {c_conf['fn_ref_true_pred_false']})` | "
            f"{fs['agreed']}/{fs['n']} ({(fs['accuracy'] or 0.0):.4f}) | "
            f"`({f_conf['tp_ref_true_pred_true']}, {f_conf['fp_ref_false_pred_true']}, {f_conf['tn_ref_false_pred_false']}, {f_conf['fn_ref_true_pred_false']})` | "
            f"{js['agreed']}/{js['n']} ({(js['accuracy'] or 0.0):.4f}) |"
        )

    lines.extend(
        [
            "",
            "### 2.1 校准分歧明细",
            "",
        ]
    )
    all_disagreements = [
        *h_all["disagreements"],
        *a_all["disagreements"],
        *s_all["disagreements"],
    ]
    if all_disagreements:
        for d in all_disagreements:
            lines.append(
                f"- `{d['calibration_id']}` (`{d['case_id']}`)：原标签 `answer_correct={d['label_answer_correct']}, "
                f"all_claims_supported={d['label_all_claims_supported']}`；Judge 判定 "
                f"`correctness={d['judge_correctness']}, faithfulness={d['judge_faithfulness']}` —— {d['judge_reasoning']}"
            )
    else:
        lines.append(
            "- 在人工评分（11份）、助手复核（12份，含 R015 源文档冲突负例）及单列合成矛盾负例（3份）上均无分歧。"
        )

    lines.extend(
        [
            "",
            "## 3. Token 用量、调用次数与耗时",
            "",
            f"- 硬性预算上限：`{usage['hard_token_budget']}` tokens / `{usage['max_completion_calls']}` 次调用",
            f"- 实际总调用次数：`{usage['total_sdk_calls']}` 次",
            f"- 实际总 Token 用量：`{usage['total_tokens']}` tokens（Prompt `{usage['prompt_tokens']}` + Completion `{usage['completion_tokens']}`）",
            f"- 端到端墙钟耗时：`{usage['execute_wall_seconds']}` 秒",
            f"- Judge 解析重试：执行阶段失败 `{e2e['judge_reliability']['parse_failure_count']}` 次 / 重试 `{e2e['judge_reliability']['documented_retry_count']}` 次；校准阶段失败 `{cal['judge_reliability']['parse_failure_count']}` 次 / 重试 `{cal['judge_reliability']['documented_retry_count']}` 次",
            "",
            "| 阶段 (`phase`) | 调用次数 | 成功 / 失败 | Prompt Tokens | Completion Tokens | Total Tokens | 累计 SDK 耗时 (ms) |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for phase, stats in sorted(usage["by_phase"].items()):
        lines.append(
            f"| `{phase}` | {stats['calls']} | {stats['ok_calls']} / {stats['error_calls']} | "
            f"{stats['prompt_tokens']} | {stats['completion_tokens']} | {stats['total_tokens']} | "
            f"{stats['elapsed_ms_total']:.1f} |"
        )

    lines.extend(["", "## 4. 解释边界与已知限制（Caveats）", ""])
    for caveat in summary["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")
    return "\n".join(lines)


def report(args: argparse.Namespace) -> dict[str, object]:
    out = args.output.resolve()
    summary = build_summary_report(out)
    markdown = render_summary_markdown(summary)

    write_json_overwrite(out / "summary.json", summary)
    (out / "summary.md").write_text(markdown, encoding="utf-8")

    if args.report_prefix is not None:
        prefix = args.report_prefix.resolve()
        prefix.parent.mkdir(parents=True, exist_ok=True)
        json_target = prefix.with_suffix(".json")
        md_target = prefix.with_suffix(".md")
        write_json_overwrite(json_target, summary)
        md_target.write_text(markdown, encoding="utf-8")

    print(
        json.dumps(
            {
                "summary_json": str(out / "summary.json"),
                "summary_md": str(out / "summary.md"),
                "answerable_correct": summary["end_to_end_evaluation"]["answerable_questions"][
                    "correctness_counts"
                ],
                "clarification_refusal_correct": summary["end_to_end_evaluation"][
                    "clarification_and_refusal_questions"
                ]["correctness_counts"],
                "total_tokens": summary["usage_and_runtime"]["total_tokens"],
                "total_calls": summary["usage_and_runtime"]["total_sdk_calls"],
            },
            ensure_ascii=False,
        )
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "execute", "calibrate", "report"])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS)
    parser.add_argument("--old-report", type=Path, default=DEFAULT_OLD_REPORT)
    parser.add_argument("--old-human-reviews", type=Path, default=DEFAULT_OLD_HUMAN_REVIEWS)
    parser.add_argument("--v012-dir", type=Path, default=DEFAULT_V012_DIR)
    parser.add_argument("--seed-vector-cache", type=Path, default=DEFAULT_SEED_VECTOR_CACHE)
    parser.add_argument("--model-dir", type=Path, default=ROOT / E5_DIRECTORY)
    parser.add_argument("--skip-e5-prewarm", action="store_true")
    parser.add_argument(
        "--with-calibrate",
        action="store_true",
        help="Run calibrate immediately after execute in the same bounded session",
    )
    parser.add_argument("--report-prefix", type=Path, default=DEFAULT_REPORT_PREFIX)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args)
    elif args.action == "execute":
        execute(args)
    elif args.action == "calibrate":
        calibrate(args)
    elif args.action == "report":
        report(args)


if __name__ == "__main__":
    main()
