"""Compare fixed BM25 retrieval with optional DeepSeek ranking on development only."""

import argparse
import hashlib
import json
from pathlib import Path

from app.chunking import TextChunker
from app.config import Settings
from app.deepseek import DeepSeekGenerator
from app.diagnostics import diagnose_retrieval
from app.evaluation import load_evaluation_cases
from app.reranking import rerank
from app.verification_evaluation import summarize_model_calls
from scripts.diagnose_retrieval import write_artifacts
from scripts.evaluate import (
    build_report,
    build_searchers,
    load_corpus,
    validate_evidence,
    validate_relevant_sources,
)


def query_key(question: str, filters: dict) -> str:
    return json.dumps([question, filters], sort_keys=True, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions", type=Path, default=Path("evaluation/questions.support.candidate.jsonl")
    )
    parser.add_argument("--documents", type=Path, default=Path("evaluation/support_corpus"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-k", type=int, default=20, choices=range(5, 51))
    parser.add_argument("--top-k", type=int, default=5, choices=range(1, 21))
    parser.add_argument("--context-char-budget", type=int, default=8000)
    parser.add_argument("--require-reviewed", action="store_true")
    args = parser.parse_args()
    if args.top_k > args.candidate_k or args.context_char_budget < 1:
        parser.error("top-k 不能超过候选数，上下文预算必须大于0")
    output = args.output.resolve()
    markdown = output.with_suffix(".md")
    journal = output.with_suffix(".trials.jsonl")
    targets = [output, output.with_suffix(".html"), markdown, journal]
    if output == args.questions.resolve() or output.is_relative_to(args.documents.resolve()):
        parser.error("报告必须保存到原始问题与语料之外")
    if output.suffix != ".json" or any(path.exists() for path in targets):
        parser.error("必须使用未存在的 .json 输出路径，保留每次原始结果")
    cases = [case for case in load_evaluation_cases(args.questions) if case.split == "development"]
    if not cases:
        parser.error("没有 development 问题")
    if args.require_reviewed and any(case.review_status != "human_verified" for case in cases):
        parser.error("开发问题尚未全部人工审核")
    validate_evidence(cases, args.documents)
    settings = Settings(
        embedding_provider="hashing",
        llm_provider="extractive",
        chunking_strategy="sections",
        chunk_size=500,
        chunk_overlap=80,
        context_char_budget=args.context_char_budget,
    )
    chunks, sources = load_corpus(args.documents, TextChunker(500, 80, strategy="sections"))
    validate_relevant_sources(cases, sources)
    base_search = build_searchers(chunks, settings, ["bm25"])["bm25"]
    generator = DeepSeekGenerator.from_env(verify_support=False)
    original, ranked, trials = {}, {}, []
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with journal.open("x", encoding="utf-8") as stream:
            for case in cases:
                if not case.should_answer or not case.evidence:
                    continue
                key = query_key(case.question, case.filters)
                if key in original:
                    continue
                pool = base_search(case.question, 50, filters=case.filters)
                candidate_pool = pool[: args.candidate_k]
                reranked, trace = rerank(case.question, candidate_pool, generator)
                original[key] = pool
                ranked[key] = [*reranked, *pool[args.candidate_k :]]
                trial = {
                    "id": case.case_id,
                    "filters": case.filters,
                    "candidate_chunk_ids": [hit.chunk.id for hit in candidate_pool],
                    "trace": trace,
                }
                trials.append(trial)
                stream.write(json.dumps(trial, ensure_ascii=False) + "\n")
                stream.flush()
                print(f"{case.case_id}: {trace['status']}", flush=True)
    finally:
        generator.client.close()

    def lookup(table):
        def search(question, k, *, filters):
            return table[query_key(question, filters)][:k]

        return search

    diagnostics = diagnose_retrieval(
        cases,
        chunks,
        {"bm25": lookup(original), "bm25_deepseek_rerank": lookup(ranked)},
        top_k=args.top_k,
        char_budget=args.context_char_budget,
    )
    transitions = {"gained": [], "lost": [], "unchanged": []}
    for case in diagnostics["cases"]:
        if case["status"] != "diagnosed":
            continue
        baseline = case["strategies"]["bm25"]["context_evidence_recall"]
        treatment = case["strategies"]["bm25_deepseek_rerank"]["context_evidence_recall"]
        transitions[
            "gained" if treatment > baseline else "lost" if treatment < baseline else "unchanged"
        ].append(case["id"])
    report = build_report(
        settings=settings,
        documents_dir=args.documents,
        questions_path=args.questions,
        document_sources=sources,
        chunks=chunks,
        cases=cases,
        top_ks=[args.top_k],
        results={},
        chunk_size=500,
        chunk_overlap=80,
    )
    report["configuration"].update(
        rerank_provider="deepseek",
        rerank_model=generator.model,
        candidate_k=args.candidate_k,
        candidate_pool_k=50,
        comparison_design="same_bm25_pool_same_context_budget",
    )
    report["diagnostics"] = diagnostics
    report["reranking"] = {
        "trials": trials,
        "transitions": transitions,
        "fallback_count": sum(t["trace"]["status"] == "fallback_original_order" for t in trials),
        "model_calls": summarize_model_calls(
            [{"response": {"model_trace": trial["trace"]}} for trial in trials]
        ),
        "scope": "Development candidate evidence coverage only; not answer accuracy or test results.",
    }
    write_artifacts(report, output)
    summaries = diagnostics["strategies"]
    lines = [
        "# BM25 与 DeepSeek 候选重排开发对照",
        "",
        "两组使用同一份 BM25 前50候选、相同 Top-K 和上下文字符上限。只重排前候选池，未生成答案。",
        "候选标注仍待人工审核；不能将覆盖率当作答案正确率或正式简历效果。",
        "",
        f"- 模型：{generator.model}；重排候选数：{args.candidate_k}；Top-K：{args.top_k}",
        f"- 上下文字符上限：{args.context_char_budget}；不保证两组实际 token 相等。",
        f"- 原始 JSON SHA256：{hashlib.sha256(output.read_bytes()).hexdigest()}",
        "",
        "| 策略 | 参与题数 | Top-K完整覆盖题数 | 最终上下文完整覆盖题数 |",
        "|---|---:|---:|---:|",
    ]
    for strategy, metrics in summaries.items():
        lines.append(
            f"| {strategy} | {metrics['evaluated_case_count']} | "
            f"{metrics['top_k_complete_case_count']} | {metrics['context_complete_case_count']} |"
        )
    lines.extend(["", "## 全部配对变化", ""])
    for label, ids in transitions.items():
        lines.append(f"- {label} ({len(ids)}): {', '.join(ids) or '无'}")
    lines.extend(
        [
            "",
            "## 实际调用与异常",
            "",
            f"- 回退原始排序：{report['reranking']['fallback_count']}",
            json.dumps(report["reranking"]["model_calls"], ensure_ascii=False, indent=2),
            "",
        ]
    )
    with markdown.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    print(json.dumps({"summaries": summaries, "transitions": transitions}, ensure_ascii=False))


if __name__ == "__main__":
    main()
