"""Paired E5 Hybrid RRF vs pinned BGE: same fixed pool, Top-5 and context budget.

配对设计：两组的候选池完全相同（同一 E5 Hybrid 结果、同一 50 个候选），
唯一差别是“是否再用 BGE 逐对打分”。因此 Top-5 与上下文覆盖的变化可以直接归因于重排。

报告同时保留 ``before`` / ``after`` 的候选 id 与 BGE 原始 logit，便于解释
“哪一条被提上来、哪一条被压下去”；CPU 计时只描述本机这一次运行。
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import perf_counter

from app.chunking import TextChunker
from app.config import Settings
from app.cross_encoder import BGEReranker, BGE_DIRECTORY
from app.diagnostics import diagnose_retrieval
from app.evaluation import load_evaluation_cases
from app.factory import create_cached_embedding
from app.local_embeddings import E5_DIRECTORY, E5_MODEL, sha256_file
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever
from scripts.evaluate import load_corpus, validate_evidence, validate_relevant_sources


def main():
    """固定候选池 → BGE 重排 → 配对诊断；历史报告与运行目录一律不可覆盖。"""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--documents", type=Path, default=Path("evaluation/support_corpus"))
    p.add_argument("--questions", type=Path, required=True)
    p.add_argument("--split", choices=["development"], default="development")
    p.add_argument("--require-reviewed", action="store_true")
    p.add_argument("--model-dir", type=Path, default=E5_DIRECTORY)
    p.add_argument("--reranker-dir", type=Path, default=BGE_DIRECTORY)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--repeats", type=int, default=1)
    args = p.parse_args()
    targets = [
        args.output,
        args.output.with_suffix(".md"),
        args.output.with_suffix(".trials.jsonl"),
    ]
    if args.output.suffix != ".json" or any(t.exists() for t in targets) or args.run_dir.exists():
        p.error("output/run-dir must be new; historical reports are immutable")
    if not 1 <= args.repeats <= 3:
        p.error("repeats must be in 1..3")
    roots = [args.documents.resolve(), args.model_dir.resolve(), args.reranker_dir.resolve()]
    if any(
        t.resolve() == args.questions.resolve() or any(t.resolve().is_relative_to(r) for r in roots)
        for t in [*targets, args.run_dir]
    ):
        p.error("outputs must be outside original questions/corpus/models")
    cases = [c for c in load_evaluation_cases(args.questions) if c.split == "development"]
    if not cases or (
        args.require_reviewed and any(c.review_status != "human_verified" for c in cases)
    ):
        p.error("empty development set or questions are not all human_verified")
    validate_evidence(cases, args.documents)
    chunks, sources = load_corpus(args.documents, TextChunker(500, 80, strategy="sections"))
    validate_relevant_sources(cases, sources)
    selected = [c for c in cases if c.should_answer and c.evidence]
    if not selected:
        p.error("no answerable development cases with evidence")
    settings = Settings(
        embedding_provider="local_e5",
        embedding_model=E5_MODEL,
        embedding_model_dir=args.model_dir,
        data_dir=args.run_dir,
        llm_provider="extractive",
        reranker_provider="none",
    )
    print("Loading E5 and indexing fixed corpus", flush=True)
    cached = create_cached_embedding(settings)
    dense, sparse = DenseRetriever(cached), BM25Retriever()
    hybrid = HybridRetriever(dense, sparse)
    hybrid.index(chunks)
    print("Loading pinned BGE cross encoder", flush=True)
    reranker = BGEReranker(args.reranker_dir)
    tables = {"e5_hybrid": {}, "e5_hybrid_bge": {}}
    trials = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    first = selected[0]
    pool = hybrid.search(first.question, 50, filters=first.filters)
    reranker.rerank(first.question, pool)  # One explicit unmeasured warmup.
    with args.output.with_suffix(".trials.jsonl").open("x", encoding="utf-8") as f:
        for case in selected:
            for repeat in range(args.repeats):
                started = perf_counter()
                pool = hybrid.search(case.question, 50, filters=case.filters)
                retrieval_ms = (perf_counter() - started) * 1000
                ranked, trace = reranker.rerank(case.question, pool)
                stable = True
                for name, hits in (("e5_hybrid", pool), ("e5_hybrid_bge", ranked)):
                    old = tables[name].setdefault(case.case_id, hits)
                    stable &= [h.chunk.id for h in old] == [h.chunk.id for h in hits]
                trial = {
                    "id": case.case_id,
                    "repeat": repeat,
                    "retrieval_ms": retrieval_ms,
                    "reranking": trace,
                    "stable": stable,
                    "before": [h.to_dict() for h in pool],
                    "after": [h.to_dict() for h in ranked],
                }
                trials.append(trial)
                f.write(json.dumps(trial, ensure_ascii=False) + "\n")
                f.flush()
            print(f"{case.case_id}: done", flush=True)
    keys = {json.dumps([c.question, c.filters], sort_keys=True): c.case_id for c in selected}

    def lookup(name):
        def search(query, k, *, filters):
            return tables[name][keys[json.dumps([query, filters], sort_keys=True)]][:k]

        return search

    diagnostics = diagnose_retrieval(
        cases,
        chunks,
        {n: lookup(n) for n in tables},
        top_k=5,
        char_budget=8000,
        context_policy="neighbors",
    )
    changes = {}
    for metric in ("top_k_evidence_recall", "context_evidence_recall"):
        groups = {"gained": [], "lost": [], "unchanged": []}
        for row in diagnostics["cases"]:
            if row["status"] != "diagnosed":
                continue
            a, b = (row["strategies"][name][metric] for name in tables)
            groups["gained" if b > a else "lost" if b < a else "unchanged"].append(row["id"])
        changes[metric] = groups
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evidence_type": "reviewed_development_evidence_coverage_not_answer_accuracy",
        "dataset": {
            "questions_sha256": sha256_file(args.questions),
            "cases": len(cases),
            "review_status": dict(Counter(c.review_status for c in cases)),
            "diagnosed": len(selected),
            "documents": len(sources),
            "chunks": len(chunks),
            "corpus_files": {s: sha256_file(args.documents / s) for s in sorted(sources)},
        },
        "configuration": {
            "candidate_k": 50,
            "top_k": 5,
            "context_char_budget": 8000,
            "context_policy": "neighbors",
            "rrf_k": 60,
            "weights": [1, 1],
            "repeats": args.repeats,
            "warmup": "first answerable query, unmeasured",
            "selection": "first 50 after RRF -> BGE -> Top-5 -> neighbors",
        },
        "embedding": cached.model.descriptor,
        "reranker": reranker.descriptor,
        "diagnostics": diagnostics,
        "transitions": changes,
        "timing": {
            "retrieval_ms": [t["retrieval_ms"] for t in trials],
            "reranking_ms": [t["reranking"]["elapsed_ms"] for t in trials],
        },
        "execution": {
            "paid_api_calls": 0,
            "generated_answers": 0,
            "held_out_cases": 0,
            "trials": len(trials),
            "unstable": sum(not t["stable"] for t in trials),
        },
        "trials_sha256": sha256_file(args.output.with_suffix(".trials.jsonl")),
        "source_sha256": {
            str(s): sha256_file(s)
            for s in (
                Path(__file__),
                Path("app/cross_encoder.py"),
                Path("app/retrieval.py"),
                Path("app/diagnostics.py"),
                Path("app/context.py"),
            )
        },
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# BGE Cross-Encoder 开发集配对对照",
        "",
        "这是已审核开发题的证据覆盖诊断；没有生成答案，没有运行保留测试。",
        "",
        "固定两路各50候选、RRF后50候选、Top-5、neighbors及8000字符上限。",
        "",
        "| 策略 | Top-5完整覆盖 | 上下文完整覆盖 |",
        "|---|---:|---:|",
    ]
    for name, s in diagnostics["strategies"].items():
        lines.append(
            f"| {name} | {s['top_k_complete_case_count']} | {s['context_complete_case_count']} |"
        )
    lines += [
        "",
        f"分母：{len(selected)}；跳过：{len(cases) - len(selected)}。",
        "",
        "```json",
        json.dumps(changes, ensure_ascii=False, indent=2),
        "```",
        "",
        f"原始JSON SHA256：`{hashlib.sha256(args.output.read_bytes()).hexdigest()}`",
        "",
    ]
    args.output.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps({"summary": diagnostics["strategies"], "changes": changes}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
