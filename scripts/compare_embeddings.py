"""Frozen development retrieval comparison: BM25, Hashing, real E5 Dense and Hybrid."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
from time import perf_counter

from app.chunking import TextChunker
from app.config import Settings
from app.context import expand_context
from app.diagnostics import diagnose_retrieval
from app.embeddings import HashingEmbedding
from app.evaluation import load_evaluation_cases
from app.factory import create_cached_embedding
from app.local_embeddings import E5_DIRECTORY, E5_MODEL, E5_REVISION
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever, matches_filters
from app.vector_cache import CachedEmbedding
from scripts.diagnose_retrieval import write_artifacts
from scripts.evaluate import build_report, load_corpus, validate_evidence, validate_relevant_sources


STRATEGIES = ("bm25", "hashing_dense", "e5_dense", "e5_hybrid")
METRICS = ("document_recall", "top_k_evidence_recall", "context_evidence_recall")


def transitions(diagnostics: dict) -> dict:
    result = {}
    rows = [c for c in diagnostics["cases"] if c["status"] == "diagnosed"]
    for baseline, candidate in (
        ("bm25", "e5_dense"),
        ("bm25", "e5_hybrid"),
        ("hashing_dense", "e5_dense"),
        ("e5_dense", "e5_hybrid"),
    ):
        pair = {}
        for metric in METRICS:
            groups = {"improved": [], "regressed": [], "unchanged": []}
            for row in rows:
                before = row["strategies"][baseline][metric]
                after = row["strategies"][candidate][metric]
                label = (
                    "improved" if after > before else "regressed" if after < before else "unchanged"
                )
                groups[label].append(row["id"])
            pair[metric] = groups
        result[f"{candidate}_vs_{baseline}"] = pair
    return result


def priority_review(diagnostics: dict) -> list[dict]:
    result = []
    for row in diagnostics["cases"]:
        if row["status"] != "diagnosed":
            continue
        s = row["strategies"]
        reasons = []
        if s["e5_hybrid"]["context_evidence_recall"] < s["bm25"]["context_evidence_recall"]:
            reasons.append("Hybrid上下文覆盖相对BM25退化")
        if s["e5_dense"]["context_evidence_recall"] < s["bm25"]["context_evidence_recall"]:
            reasons.append("Dense上下文覆盖相对BM25退化")
        if s["e5_hybrid"]["context_evidence_recall"] < 1:
            reasons.append("Hybrid上下文仍未完整覆盖")
        if s["e5_dense"]["context_evidence_recall"] > s["bm25"]["context_evidence_recall"]:
            reasons.append("Dense新增覆盖需核对标注充分性")
        if reasons:
            result.append(
                {
                    "id": row["id"],
                    "question": row["question"],
                    "filters": row["filters"],
                    "review_status": row["review_status"],
                    "reasons": reasons,
                    "evidence": row["evidence"],
                    "coverage": {name: s[name]["context_evidence_recall"] for name in STRATEGIES},
                    "outcomes": {
                        name: [a["outcome"] for a in s[name]["anchors"]] for name in STRATEGIES
                    },
                }
            )
    return sorted(
        result,
        key=lambda r: (
            not any("退化" in reason for reason in r["reasons"]),
            r["coverage"]["e5_hybrid"] == 1,
            r["id"],
        ),
    )


def distribution(values: list[float]) -> dict:
    import math

    ordered = sorted(values)
    return {
        "count": len(values),
        "total": sum(values),
        "p50": ordered[max(0, math.ceil(len(values) * 0.5) - 1)],
        "p95": ordered[max(0, math.ceil(len(values) * 0.95) - 1)],
    }


def render_markdown(report: dict, raw_sha: str) -> str:
    lines = [
        "# 本地语义 Embedding 检索开发对照",
        "",
        "固定开发候选，未生成答案；标注仍待人工审核，覆盖不是答案正确率。保留测试集未运行。",
        "",
        f"原始JSON SHA256：`{raw_sha}`",
        "",
        "| 策略 | 文档命中 | Top-5完整证据 | 上下文完整证据 | 查询P50/P95 ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, summary in report["diagnostics"]["strategies"].items():
        t = report["timing"]["query"][name]["retrieval_ms"]
        lines.append(
            f"| {name} | {summary['document_hit_case_count']} | "
            f"{summary['top_k_complete_case_count']} | {summary['context_complete_case_count']} | "
            f"{t['p50']:.2f} / {t['p95']:.2f} |"
        )
    statuses = report["diagnostics"]["case_status_counts"]
    lines += [
        "",
        f"分母为{statuses.get('diagnosed', 0)}道可回答且有证据范围的开发候选；"
        f"其余{report['dataset']['case_count'] - statuses.get('diagnosed', 0)}题跳过检索效果诊断。",
        "",
        "## 固定条件与计时",
        "",
        f"{report['dataset']['document_count']}份固定官方文档、{report['dataset']['chunk_count']}分块；"
        "sections 500/80；产品/版本预过滤；Top-5；",
        "neighbors（种子优先，再同章节前后各一块）；8000正文字符上限；",
        "RRF k=60、等权、两路各50候选。相同上限不代表实际字符/token相同。",
        "查询计时包含在线查询向量、Python余弦线性扫描/排序或BM25/RRF；取最终前50再截Top-5。",
        f"每策略单独暖机1次；逐题轮换策略顺序，重复{report['configuration']['repeats']}次。"
        "上下文计时单列，诊断和I/O不计入查询。",
        "",
        "```json",
        json.dumps(report["timing"]["build"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 全部配对变化",
        "",
    ]
    for pair, metrics in report["transitions"].items():
        lines.append(f"### {pair}")
        for metric, groups in metrics.items():
            lines.append(f"- {metric}")
            for label, ids in groups.items():
                lines.append(f"  - {label} ({len(ids)}): {', '.join(ids) or '无'}")
        lines.append("")
    lines += ["## 人工优先复核（仅建议顺序，无自动签审）", ""]
    for row in report["priority_review"]:
        lines += [
            f"### {row['id']}：{row['question']}",
            "",
            f"- 原因：{'；'.join(row['reasons'])}",
            f"- 上下文覆盖：{json.dumps(row['coverage'], ensure_ascii=False)}",
            f"- 诊断阶段：{json.dumps(row['outcomes'], ensure_ascii=False)}",
        ]
        for a in row["evidence"]:
            source = Path(report["configuration"]["documents_dir"]) / a["source"]
            text = TextChunker._normalize(source.read_text(encoding="utf-8"))
            line = text[: a["start_char"]].count("\n") + 1
            lines += [
                f"- 原文：[{a['source']}:{line}](../../{source.as_posix()})；"
                f"Python字符半开区间 [{a['start_char']}, {a['end_char']})",
                "",
                "```text",
                a["quote"],
                "```",
                "",
            ]
    lines += [
        "## 资源与可复现性",
        "",
        "```json",
        json.dumps(report["resources"], ensure_ascii=False, indent=2),
        "```",
        "",
        "无LLM/API调用；模型和SQLite缓存留本地。查询向量不落盘；所有原始重复试验保存在同名trials.jsonl。",
        "浮点结果允许小数误差；报告额外检查三次Top-50排序是否相同。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, default=Path("evaluation/support_corpus"))
    parser.add_argument(
        "--questions", type=Path, default=Path("evaluation/questions.support.candidate.jsonl")
    )
    parser.add_argument("--split", choices=["development"], default="development")
    parser.add_argument("--model-dir", type=Path, default=E5_DIRECTORY)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    targets = [
        args.output,
        args.output.with_suffix(".md"),
        args.output.with_suffix(".html"),
        args.output.with_suffix(".trials.jsonl"),
    ]
    if args.output.suffix != ".json" or any(p.exists() for p in targets) or args.run_dir.exists():
        parser.error("输出及run-dir必须尚不存在，保留原始试验并确保冷缓存")
    if args.repeats < 1:
        parser.error("repeats必须大于0")
    roots = [args.documents.resolve(), args.model_dir.resolve()]
    if any(
        p.resolve() == args.questions.resolve() or any(p.resolve().is_relative_to(r) for r in roots)
        for p in [*targets, args.run_dir]
    ):
        parser.error("实验产物必须位于原始问题/语料/模型之外")
    cases = [c for c in load_evaluation_cases(args.questions) if c.split == "development"]
    if not cases:
        parser.error("开发集为空")
    validate_evidence(cases, args.documents)
    settings = Settings(
        embedding_provider="local_e5",
        embedding_model=E5_MODEL,
        embedding_model_revision=E5_REVISION,
        embedding_model_dir=args.model_dir,
        embedding_device="cpu",
        embedding_batch_size=8,
        embedding_cpu_threads=4,
        embedding_max_length=512,
        embedding_cache_revision="1",
        llm_provider="extractive",
        chunking_strategy="sections",
        chunk_size=500,
        chunk_overlap=80,
        context_char_budget=8000,
        context_policy="neighbors",
        data_dir=args.run_dir,
    )
    import psutil

    process = psutil.Process()

    def memory():
        m = process.memory_info()
        return {"rss_bytes": m.rss, "peak_working_set_bytes": getattr(m, "peak_wset", None)}

    resources = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "logical_cpus": psutil.cpu_count(),
        "physical_cpus": psutil.cpu_count(logical=False),
        "ram_total_bytes": psutil.virtual_memory().total,
        "before": memory(),
        "packages": {
            p: version(p) for p in ("torch", "transformers", "tokenizers", "numpy", "psutil")
        },
    }
    started = perf_counter()
    chunks, sources = load_corpus(args.documents, TextChunker(500, 80, strategy="sections"))
    build = {"corpus_parse_chunk_ms": (perf_counter() - started) * 1000}
    validate_relevant_sources(cases, sources)
    started = perf_counter()
    sparse = BM25Retriever()
    sparse.index(chunks)
    build["bm25_index_ms"] = (perf_counter() - started) * 1000
    started = perf_counter()
    hashing = CachedEmbedding(
        HashingEmbedding(), args.run_dir / "hashing.sqlite3", "hashing-384-v1"
    )
    hash_dense = DenseRetriever(hashing)
    hash_dense.index(chunks)
    build["hashing_cold_index_ms"] = (perf_counter() - started) * 1000
    build["hashing_cache_stats"] = dict(hashing.last_stats)
    print("Loading pinned local E5", flush=True)
    started = perf_counter()
    cached = create_cached_embedding(settings)
    build["e5_load_and_file_verification_ms"] = (perf_counter() - started) * 1000
    raw_model = cached.model
    resources["after_model_load"] = memory()
    started = perf_counter()
    dense = DenseRetriever(cached)
    dense.index(chunks)
    build["e5_cold_document_encoding_index_ms"] = (perf_counter() - started) * 1000
    build["e5_cold_cache_stats"] = dict(cached.last_stats)
    build["e5_document_input_stats"] = raw_model.input_stats([c.text for c in chunks], "passage: ")
    started = perf_counter()
    warm_dense = DenseRetriever(cached)
    warm_dense.index(chunks)
    build["e5_warm_cache_index_ms"] = (perf_counter() - started) * 1000
    build["e5_warm_cache_stats"] = dict(cached.last_stats)
    if dense.vectors != warm_dense.vectors:
        raise ValueError("冷/热缓存向量不一致")
    del warm_dense
    searchers = {
        "bm25": sparse.search,
        "hashing_dense": hash_dense.search,
        "e5_dense": dense.search,
        "e5_hybrid": HybridRetriever(dense, sparse).search,
    }
    selected = [c for c in cases if c.should_answer and c.evidence]
    build["e5_query_input_stats"] = raw_model.input_stats([c.question for c in selected], "query: ")
    first = selected[0]
    warmup = {}
    for name, search in searchers.items():
        started = perf_counter()
        search(first.question, 50, filters=first.filters)
        warmup[name] = (perf_counter() - started) * 1000
    tables = {name: {} for name in STRATEGIES}
    trials = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.with_suffix(".trials.jsonl").open("x", encoding="utf-8") as stream:
        for index, case in enumerate(selected):
            eligible = [c for c in chunks if matches_filters(c, case.filters)]
            for repeat in range(args.repeats):
                offset = (index + repeat) % len(STRATEGIES)
                order = STRATEGIES[offset:] + STRATEGIES[:offset]
                for name in order:
                    started = perf_counter()
                    pool = searchers[name](case.question, 50, filters=case.filters)
                    retrieval_ms = (perf_counter() - started) * 1000
                    started = perf_counter()
                    expand_context(pool[:5], eligible, char_budget=8000, policy="neighbors")
                    context_ms = (perf_counter() - started) * 1000
                    previous = tables[name].setdefault(case.case_id, pool)
                    stable = [h.chunk.id for h in previous] == [h.chunk.id for h in pool]
                    trial = {
                        "id": case.case_id,
                        "strategy": name,
                        "repeat": repeat,
                        "order": list(order),
                        "retrieval_ms": retrieval_ms,
                        "context_assembly_ms": context_ms,
                        "same_ranking_as_first": stable,
                        "pool": [
                            {
                                "chunk_id": h.chunk.id,
                                "score": h.score,
                                "dense_rank": h.dense_rank,
                                "sparse_rank": h.sparse_rank,
                            }
                            for h in pool
                        ],
                    }
                    trials.append(trial)
                    stream.write(json.dumps(trial, ensure_ascii=False) + "\n")
                    stream.flush()
            print(f"{case.case_id}: {args.repeats} repeats x 4 strategies", flush=True)
    keys = {json.dumps([c.question, c.filters], sort_keys=True): c.case_id for c in selected}

    def lookup(name):
        def search(question, k, *, filters):
            return tables[name][keys[json.dumps([question, filters], sort_keys=True)]][:k]

        return search

    diagnostics = diagnose_retrieval(
        cases,
        chunks,
        {n: lookup(n) for n in STRATEGIES},
        top_k=5,
        char_budget=8000,
        context_policy="neighbors",
    )
    for name, summary in diagnostics["strategies"].items():
        values = [c["strategies"][name] for c in diagnostics["cases"] if c["status"] == "diagnosed"]
        for metric in METRICS:
            summary[f"mean_{metric}"] = sum(v[metric] for v in values) / len(values)
        summary["context_chars_total"] = sum(v["context_chars"] for v in values)
    report = build_report(
        settings=settings,
        documents_dir=args.documents,
        questions_path=args.questions,
        document_sources=sources,
        chunks=chunks,
        cases=cases,
        top_ks=[5],
        results={},
        chunk_size=500,
        chunk_overlap=80,
    )
    report["configuration"].update(
        comparison_design="fixed_development_retrieval_only",
        rrf_k=60,
        rrf_weights=[1, 1],
        repeats=args.repeats,
        strategy_order="rotate by case index plus repetition",
        query_vector_cache=False,
    )
    resources["after_trials"] = memory()
    resources["model_file_bytes"] = sum(f["bytes"] for f in raw_model.manifest["files"].values())
    resources["cache_file_bytes"] = {
        p.name: p.stat().st_size for p in args.run_dir.glob("*.sqlite3")
    }
    resources["document_vector_float32_payload_bytes"] = len(chunks) * 384 * 4
    report.update(
        diagnostics=diagnostics,
        model=raw_model.descriptor,
        transitions=transitions(diagnostics),
        priority_review=priority_review(diagnostics),
        resources=resources,
        timing={
            "build": build,
            "warmup_ms": warmup,
            "query": {
                name: {
                    metric: distribution([t[metric] for t in trials if t["strategy"] == name])
                    for metric in ("retrieval_ms", "context_assembly_ms")
                }
                for name in STRATEGIES
            },
        },
        execution={
            "model_api_calls": 0,
            "answer_generation_count": 0,
            "test_case_count": 0,
            "trial_count": len(trials),
            "ranking_changed_trial_count": sum(not t["same_ranking_as_first"] for t in trials),
            "case_review_status_counts": dict(Counter(c.review_status for c in cases)),
            "trials_sha256": hashlib.sha256(
                args.output.with_suffix(".trials.jsonl").read_bytes()
            ).hexdigest(),
        },
    )
    write_artifacts(report, args.output)
    raw_sha = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(".md").write_text(render_markdown(report, raw_sha), encoding="utf-8")
    print(
        json.dumps(
            {"summary": diagnostics["strategies"], "timing": report["timing"]["query"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
