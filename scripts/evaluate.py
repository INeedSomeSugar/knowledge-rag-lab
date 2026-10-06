from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.chunking import TextChunker
from app.config import Settings
from app.domain import Chunk, SearchHit
from app.evaluation import EvaluationCase, evaluate_strategies, load_evaluation_cases
from app.evaluation import evaluate_answer_behavior
from app.corpus import read_documents
from app.factory import create_cached_embedding
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever


SearchFunction = Callable[[str, int], list[SearchHit]]


def load_corpus(directory: Path, chunker: TextChunker) -> tuple[list[Chunk], list[str]]:
    if not directory.is_dir():
        raise ValueError(f"文档目录不存在：{directory}")

    chunks: list[Chunk] = []
    sources: list[str] = []
    for source, text, metadata in read_documents(directory):
        document_id = hashlib.sha1(f"{source}\0{text}".encode("utf-8")).hexdigest()[:16]
        document_chunks = chunker.split(document_id, source, text)
        for chunk in document_chunks:
            chunk.metadata.update(metadata)
        chunks.extend(document_chunks)
        sources.append(source)
    if not chunks:
        raise ValueError("所有文档解析后均为空")
    return chunks, sources


def build_searchers(
    chunks: list[Chunk], settings: Settings, strategies: list[str]
) -> dict[str, SearchFunction]:
    selected = list(dict.fromkeys(strategies))
    supported = {"bm25", "dense", "hybrid"}
    unknown = set(selected) - supported
    if unknown:
        raise ValueError(f"不支持的检索策略：{', '.join(sorted(unknown))}")

    needs_dense = bool({"dense", "hybrid"}.intersection(selected))
    needs_sparse = bool({"bm25", "hybrid"}.intersection(selected))
    dense = DenseRetriever(create_cached_embedding(settings)) if needs_dense else None
    sparse = BM25Retriever() if needs_sparse else None
    if dense is not None:
        dense.index(chunks)
    if sparse is not None:
        sparse.index(chunks)

    searchers: dict[str, SearchFunction] = {}
    if "bm25" in selected and sparse is not None:
        searchers["bm25"] = sparse.search
    if "dense" in selected and dense is not None:
        searchers["dense"] = dense.search
    if "hybrid" in selected and dense is not None and sparse is not None:
        searchers["hybrid"] = HybridRetriever(dense, sparse).search
    return searchers


def validate_relevant_sources(cases: list[EvaluationCase], document_sources: list[str]) -> None:
    available = set(document_sources)
    missing = sorted(
        {source for case in cases for source in case.relevant_sources if source not in available}
    )
    if missing:
        raise ValueError("评测标注引用了文档目录中不存在的来源：" + ", ".join(missing))


def _build_warnings(
    *,
    document_count: int,
    chunk_count: int,
    case_count: int,
    results: dict[str, dict[str, dict[str, object]]],
) -> list[str]:
    warnings: list[str] = []
    if document_count < 10:
        warnings.append("文档少于 10 份，当前结果只适合作为冒烟测试。")
    if chunk_count < 20:
        warnings.append("分块少于 20 个，检索候选空间过小，指标可能虚高。")
    if case_count < 50:
        warnings.append("评测问题少于 50 个，当前数字不应写入简历。")

    signatures = []
    for strategy_results in results.values():
        signatures.append(
            tuple(
                (
                    metrics.get(f"hit_rate@{top_k}"),
                    metrics.get(f"recall@{top_k}"),
                    metrics.get(f"mrr@{top_k}"),
                    metrics.get(f"ndcg@{top_k}"),
                )
                for top_k, metrics in sorted(
                    strategy_results.items(), key=lambda item: int(item[0])
                )
            )
        )
    if len(signatures) > 1 and len(set(signatures)) == 1:
        warnings.append("所有检索策略指标完全相同，评测集暂时无法区分策略优劣。")
    return warnings


def build_report(
    *,
    settings: Settings,
    documents_dir: Path,
    questions_path: Path,
    document_sources: list[str],
    chunks: list[Chunk],
    cases: list[EvaluationCase],
    top_ks: list[int],
    results: dict[str, dict[str, dict[str, object]]],
    chunk_size: int,
    chunk_overlap: int,
) -> dict[str, object]:
    answerable_count = sum(1 for case in cases if case.should_answer and case.relevant_sources)
    categories = Counter(case.category for case in cases)
    warnings = _build_warnings(
        document_count=len(document_sources),
        chunk_count=len(chunks),
        case_count=len(cases),
        results=results,
    )
    if any(case.review_status != "human_verified" for case in cases):
        warnings.append("包含未经人工核验的问题：结果仅为开发诊断，不可作为正式效果或简历数字。")
    if any(not case.evidence for case in cases if case.should_answer):
        warnings.append("部分可回答问题未标注证据范围；文档级命中不代表找到答案。")
    return {
        "schema_version": "2.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "embedding_provider": settings.embedding_provider,
            "embedding_model": "hashing-384"
            if settings.embedding_provider == "hashing"
            else settings.embedding_model,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "chunking_strategy": settings.chunking_strategy,
            "rrf_candidate_k": 50,
            "top_ks": sorted(set(top_ks)),
            "documents_dir": documents_dir.as_posix(),
            "questions_path": questions_path.as_posix(),
            "embedding_cache_revision": settings.embedding_cache_revision,
            "context_char_budget": settings.context_char_budget,
            "context_policy": settings.context_policy,
        },
        "dataset": {
            "document_count": len(document_sources),
            "chunk_count": len(chunks),
            "case_count": len(cases),
            "answerable_case_count": answerable_count,
            "unanswerable_case_count": len(cases) - answerable_count,
            "categories": dict(sorted(categories.items())),
            "human_verified_count": sum(case.review_status == "human_verified" for case in cases),
            "evidence_annotated_count": sum(bool(case.evidence) for case in cases),
        },
        "warnings": warnings,
        "results": results,
        "provenance": {
            "code_sha256": code_fingerprint(),
            "questions_sha256": hashlib.sha256(questions_path.read_bytes()).hexdigest(),
            "corpus_sha256": hashlib.sha256(
                json.dumps(
                    [
                        (source, hashlib.sha256(text.encode()).hexdigest(), metadata)
                        for source, text, metadata in read_documents(documents_dir)
                    ],
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest(),
            "chunks_sha256": hashlib.sha256(
                json.dumps(
                    [chunk.to_dict() for chunk in chunks], sort_keys=True, ensure_ascii=False
                ).encode()
            ).hexdigest(),
            "selected_case_ids": [case.case_id for case in cases],
            "splits": sorted({case.split for case in cases}),
        },
    }


def render_markdown(report: dict[str, object]) -> str:
    configuration = report["configuration"]
    dataset = report["dataset"]
    results = report["results"]
    assert isinstance(configuration, dict)
    assert isinstance(dataset, dict)
    assert isinstance(results, dict)

    lines = [
        "# 检索评测报告",
        "",
        f"- Embedding：`{configuration['embedding_provider']}` / `{configuration['embedding_model']}`",
        f"- 分块：{configuration['chunk_size']} 字符，重叠 {configuration['chunk_overlap']} 字符",
        f"- 数据规模：{dataset['document_count']} 份文档，{dataset['chunk_count']} 个分块，{dataset['case_count']} 个问题",
        "",
        "## 指标汇总",
        "",
        "| 策略 | K | Hit@K | Recall@K | MRR@K | nDCG@K | 证据 Recall@K |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, strategy_results in results.items():
        assert isinstance(strategy_results, dict)
        for top_k, metrics in sorted(strategy_results.items(), key=lambda item: int(item[0])):
            assert isinstance(metrics, dict)
            lines.append(
                f"| {strategy} | {top_k} | "
                f"{metrics[f'hit_rate@{top_k}']:.4f} | "
                f"{metrics[f'recall@{top_k}']:.4f} | "
                f"{metrics[f'mrr@{top_k}']:.4f} | "
                f"{metrics[f'ndcg@{top_k}']:.4f} | "
                + (
                    f"{metrics[f'evidence_recall@{top_k}']:.4f}"
                    if metrics.get(f"evidence_recall@{top_k}") is not None
                    else "未标注"
                )
                + " |"
            )

    warnings = report.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "## 使用限制", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    answers = report.get("answers")
    if isinstance(answers, dict):
        lines.extend(["", "## 回答行为（不代表答案正确率）", ""])
        for key, value in answers.items():
            if key not in {"details", "note", "model_calls"}:
                lines.append(f"- {key}: {value}")
        lines.append(str(answers["note"]))
    comparison = report.get("verification_comparison")
    if isinstance(comparison, dict):
        lines.extend(["", "## 同一答案的核验开关对照", "", comparison["note"], ""])
        lines.extend(
            [
                "| 核验 | 回答数 | 误拒答率（行为） | 核验错误数 | 人工正确率 |",
                "|---|---:|---:|---:|---|",
            ]
        )
        for label, metrics in (("关闭", comparison["without_verification"]), ("开启", answers)):
            answered = sum(item["response"]["status"] == "answered" for item in metrics["details"])
            lines.append(
                f"| {label} | {answered} | {metrics['false_refusal_rate']} | "
                f"{metrics['model_calls']['stages']['verification']['error_count']} | 未评分 |"
            )
        lines.extend(["", "### 实际模型调用", "", answers["model_calls"]["note"], ""])
        for stage, metrics in answers["model_calls"]["stages"].items():
            lines.append(f"- {stage}: `{json.dumps(metrics, ensure_ascii=False)}`")
    lines.append("")
    return "\n".join(lines)


def write_report(
    report: dict[str, object], output_dir: Path, report_name: str
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report_name}.json"
    markdown_path = output_dir / f"{report_name}.md"
    mode = "x" if "verification_comparison" in report else "w"
    with json_path.open(mode, encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2))
    with markdown_path.open(mode, encoding="utf-8") as stream:
        stream.write(render_markdown(report))
    return json_path, markdown_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="比较 BM25、Dense 与 RRF 混合检索")
    parser.add_argument("--documents", type=Path, default=Path("sample_data"))
    parser.add_argument("--questions", type=Path, default=Path("evaluation/questions.jsonl"))
    parser.add_argument("--top-k", type=int, nargs="+", default=[1, 3, 5])
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=["bm25", "dense", "hybrid"],
        default=["bm25", "dense", "hybrid"],
    )
    parser.add_argument("--chunk-size", type=int)
    parser.add_argument("--chunk-overlap", type=int)
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/reports"))
    parser.add_argument("--report-name", default="latest")
    parser.add_argument("--chunking-strategy", choices=["window", "sections"])
    parser.add_argument(
        "--answers", action="store_true", help="同时运行回答状态评测；真实模型会产生 API 请求"
    )
    parser.add_argument("--split", choices=["development", "test"])
    parser.add_argument("--require-reviewed", action="store_true")
    parser.add_argument("--demo", action="store_true", help="强制零密钥模式，不调用模型 API")
    parser.add_argument("--answer-strategy", choices=["bm25", "dense", "hybrid"])
    parser.add_argument("--context-char-budget", type=int)
    parser.add_argument("--context-policy", choices=["neighbors", "merged_neighbors"])
    parser.add_argument("--verification", choices=["on", "off"], default=None)
    parser.add_argument(
        "--compare-verification",
        action="store_true",
        help="开发集配对实验：同一初始答案分别关闭/开启核验，需要 --answers 和真实模型",
    )
    return parser.parse_args()


def main() -> None:
    from dataclasses import replace

    args = parse_args()
    settings = Settings()
    if args.demo:
        from dataclasses import replace

        settings = replace(
            settings,
            embedding_provider="hashing",
            llm_provider="extractive",
            retrieval_strategy="bm25",
            data_dir=Path("data/demo-index"),
        )
    if args.chunking_strategy:
        from dataclasses import replace

        settings = replace(settings, chunking_strategy=args.chunking_strategy)
    if args.answer_strategy:
        settings = replace(settings, retrieval_strategy=args.answer_strategy)
    if args.context_char_budget is not None:
        settings = replace(settings, context_char_budget=args.context_char_budget)
    if args.context_policy is not None:
        settings = replace(settings, context_policy=args.context_policy)
    if args.verification is not None:
        settings = replace(settings, llm_verify_support=args.verification == "on")
    if args.compare_verification:
        if not args.answers or args.demo or settings.llm_provider == "extractive":
            raise ValueError("配对核验实验需要 --answers 和真实生成模型，不能使用 --demo")
        if args.split != "development" or args.verification is not None:
            raise ValueError(
                "配对核验实验仅支持显式 --split development，不能同时指定 --verification"
            )
        if any(
            (args.output_dir / f"{args.report_name}.{suffix}").exists() for suffix in ("json", "md")
        ):
            raise ValueError("配对报告已存在，请使用新 --report-name 保留原始实验")
        settings = replace(settings, llm_verify_support=False)
    settings.validate()
    chunk_size = args.chunk_size if args.chunk_size is not None else settings.chunk_size
    chunk_overlap = args.chunk_overlap if args.chunk_overlap is not None else settings.chunk_overlap
    chunker = TextChunker(chunk_size, chunk_overlap, strategy=settings.chunking_strategy)
    chunks, sources = load_corpus(args.documents, chunker)
    cases = load_evaluation_cases(args.questions)
    if not args.split and len({case.split for case in cases}) > 1:
        raise ValueError("问题文件包含多个划分，请显式选择 --split development 或 test")
    if args.split:
        cases = [case for case in cases if case.split == args.split]
    if not cases:
        raise ValueError("所选划分没有问题")
    if args.require_reviewed and any(case.review_status != "human_verified" for case in cases):
        raise ValueError("正式评测要求全部问题已经人工核验")
    validate_evidence(cases, args.documents)
    validate_relevant_sources(cases, sources)
    searchers = build_searchers(chunks, settings, args.strategies)
    results = evaluate_strategies(cases, searchers, args.top_k)
    report = build_report(
        settings=settings,
        documents_dir=args.documents,
        questions_path=args.questions,
        document_sources=sources,
        chunks=chunks,
        cases=cases,
        top_ks=args.top_k,
        results=results,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    if args.answers:
        from app.factory import create_answer_generator
        from app.service import RAGService
        from app.storage import JsonChunkRepository
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            repository = JsonChunkRepository(Path(directory))
            repository.save(chunks)
            retriever = HybridRetriever(
                DenseRetriever(create_cached_embedding(settings)), BM25Retriever()
            )
            generator = create_answer_generator(settings)
            service = RAGService(
                chunker=chunker,
                retriever=retriever,
                generator=generator,
                repository=repository,
                retrieval_strategy=settings.retrieval_strategy,
                context_char_budget=settings.context_char_budget,
                context_policy=settings.context_policy,
            )
            if args.compare_verification:
                from app.verification_evaluation import evaluate_verification_pair

                report["answers"], report["verification_comparison"] = evaluate_verification_pair(
                    cases, service, generator, max(args.top_k)
                )
            else:
                from app.verification_evaluation import summarize_model_calls

                report["answers"] = evaluate_answer_behavior(cases, service.answer, max(args.top_k))
                report["answers"]["model_calls"] = summarize_model_calls(
                    report["answers"]["details"]
                )
            report["configuration"]["llm_provider"] = settings.llm_provider
            report["configuration"]["llm_model"] = settings.llm_model
            report["configuration"]["llm_enable_thinking"] = settings.llm_enable_thinking
            report["configuration"]["answer_retrieval_strategy"] = settings.retrieval_strategy
            report["configuration"]["verification_mode"] = (
                "not_applicable"
                if settings.llm_provider == "extractive"
                else "paired"
                if args.compare_verification
                else "on"
                if settings.llm_verify_support
                else "off"
            )
    json_path, markdown_path = write_report(report, args.output_dir, args.report_name)
    print(render_markdown(report))
    print(f"JSON 报告：{json_path}")
    print(f"Markdown 报告：{markdown_path}")


def validate_evidence(cases: list[EvaluationCase], directory: Path) -> None:
    texts = {source: TextChunker._normalize(text) for source, text, _ in read_documents(directory)}
    for case in cases:
        for anchor in case.evidence:
            text = texts.get(str(anchor["source"]), "")
            if text[int(anchor["start_char"]) : int(anchor["end_char"])] != anchor["quote"]:
                raise ValueError(f"证据与原文不匹配：{case.case_id}")


def code_fingerprint() -> str:
    root = Path(__file__).resolve().parent.parent
    paths = sorted(
        [
            *root.joinpath("app").rglob("*.py"),
            *root.joinpath("scripts").glob("*.py"),
            root / "pyproject.toml",
        ]
    )
    value = [
        (path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in paths
    ]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


if __name__ == "__main__":
    main()
