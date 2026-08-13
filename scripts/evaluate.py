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
from app.factory import create_embedding_model
from app.loaders import SUPPORTED_SUFFIXES, load_bytes
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever


SearchFunction = Callable[[str, int], list[SearchHit]]


def load_corpus(directory: Path, chunker: TextChunker) -> tuple[list[Chunk], list[str]]:
    if not directory.is_dir():
        raise ValueError(f"文档目录不存在：{directory}")

    chunks: list[Chunk] = []
    sources: list[str] = []
    paths = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not paths:
        raise ValueError(f"文档目录中没有支持的文件：{directory}")

    for path in paths:
        source = path.relative_to(directory).as_posix()
        text = load_bytes(path.name, path.read_bytes()).strip()
        if not text:
            continue
        document_id = hashlib.sha1(f"{source}\0{text}".encode("utf-8")).hexdigest()[:16]
        chunks.extend(chunker.split(document_id, source, text))
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
    dense = DenseRetriever(create_embedding_model(settings)) if needs_dense else None
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


def validate_relevant_sources(
    cases: list[EvaluationCase], document_sources: list[str]
) -> None:
    available = set(document_sources)
    missing = sorted(
        {
            source
            for case in cases
            for source in case.relevant_sources
            if source not in available
        }
    )
    if missing:
        raise ValueError(
            "评测标注引用了文档目录中不存在的来源：" + ", ".join(missing)
        )


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
    answerable_count = sum(
        1 for case in cases if case.should_answer and case.relevant_sources
    )
    categories = Counter(case.category for case in cases)
    warnings = _build_warnings(
        document_count=len(document_sources),
        chunk_count=len(chunks),
        case_count=len(cases),
        results=results,
    )
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "top_ks": sorted(set(top_ks)),
            "documents_dir": documents_dir.as_posix(),
            "questions_path": questions_path.as_posix(),
        },
        "dataset": {
            "document_count": len(document_sources),
            "chunk_count": len(chunks),
            "case_count": len(cases),
            "answerable_case_count": answerable_count,
            "unanswerable_case_count": len(cases) - answerable_count,
            "categories": dict(sorted(categories.items())),
        },
        "warnings": warnings,
        "results": results,
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
        "| 策略 | K | Hit@K | Recall@K | MRR@K | nDCG@K |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for strategy, strategy_results in results.items():
        assert isinstance(strategy_results, dict)
        for top_k, metrics in sorted(
            strategy_results.items(), key=lambda item: int(item[0])
        ):
            assert isinstance(metrics, dict)
            lines.append(
                f"| {strategy} | {top_k} | "
                f"{metrics[f'hit_rate@{top_k}']:.4f} | "
                f"{metrics[f'recall@{top_k}']:.4f} | "
                f"{metrics[f'mrr@{top_k}']:.4f} | "
                f"{metrics[f'ndcg@{top_k}']:.4f} |"
            )

    warnings = report.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "## 使用限制", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)


def write_report(
    report: dict[str, object], output_dir: Path, report_name: str
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report_name}.json"
    markdown_path = output_dir / f"{report_name}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="比较 BM25、Dense 与 RRF 混合检索")
    parser.add_argument("--documents", type=Path, default=Path("sample_data"))
    parser.add_argument(
        "--questions", type=Path, default=Path("evaluation/questions.jsonl")
    )
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings()
    settings.validate()
    chunk_size = args.chunk_size if args.chunk_size is not None else settings.chunk_size
    chunk_overlap = (
        args.chunk_overlap if args.chunk_overlap is not None else settings.chunk_overlap
    )
    chunker = TextChunker(chunk_size, chunk_overlap)
    chunks, sources = load_corpus(args.documents, chunker)
    cases = load_evaluation_cases(args.questions)
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
    json_path, markdown_path = write_report(report, args.output_dir, args.report_name)
    print(render_markdown(report))
    print(f"JSON 报告：{json_path}")
    print(f"Markdown 报告：{markdown_path}")


if __name__ == "__main__":
    main()
