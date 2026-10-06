"""Generate a development-only retrieval diagnostic report and portable workbench."""

import argparse
import hashlib
import json
from pathlib import Path

from app.chunking import TextChunker
from app.config import Settings
from app.diagnostics import OUTCOMES, POOL_K, diagnose_retrieval
from app.evaluation import load_evaluation_cases
from scripts.evaluate import (
    build_report,
    build_searchers,
    load_corpus,
    validate_evidence,
    validate_relevant_sources,
)


TEMPLATE = Path(__file__).resolve().parents[1] / "app/static/diagnostics.html"


def render_html(report: dict) -> str:
    # JSON in a non-executable script still needs '<' escaped to prevent </script> injection.
    data = json.dumps(report, ensure_ascii=False).replace("<", "\\u003c")
    return TEMPLATE.read_text(encoding="utf-8").replace("__DIAGNOSTIC_DATA__", data)


def write_artifacts(report: dict, output: Path) -> tuple[Path, Path]:
    if output.suffix.lower() != ".json":
        raise ValueError("输出路径必须以 .json 结尾")
    html_path = output.with_suffix(".html")
    if output.exists() or html_path.exists():
        raise FileExistsError("诊断报告已存在，请使用新文件名保留原始结果")
    raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    html = render_html(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(raw)
    with html_path.open("x", encoding="utf-8") as stream:
        stream.write(html)
    return output, html_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, default=Path("evaluation/support_corpus"))
    parser.add_argument(
        "--questions", type=Path, default=Path("evaluation/questions.support.candidate.jsonl")
    )
    parser.add_argument("--split", choices=["development"], default="development")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--chunk-overlap", type=int, default=80)
    parser.add_argument("--chunking-strategy", choices=["window", "sections"], default="sections")
    parser.add_argument("--context-char-budget", type=int, default=16000)
    parser.add_argument("--context-policy", choices=["neighbors", "merged_neighbors"],
                        default="neighbors")
    parser.add_argument("--strategies", nargs="+", choices=["bm25", "dense", "hybrid"],
                        default=["bm25", "dense", "hybrid"])
    parser.add_argument("--require-reviewed", action="store_true")
    parser.add_argument(
        "--configured-embeddings", action="store_true",
        help="显式使用 .env 中的 Embedding 配置；可能请求模型。默认强制离线 Hashing。",
    )
    parser.add_argument("--output", type=Path, default=Path("work/diagnostics/development.json"))
    args = parser.parse_args()
    if not 1 <= args.top_k <= POOL_K or args.context_char_budget < 1:
        parser.error("top-k 必须为 1..50，上下文预算必须大于 0")
    if args.output.suffix.lower() != ".json":
        parser.error("输出路径必须以 .json 结尾")
    if args.output.exists() or args.output.with_suffix(".html").exists():
        parser.error("报告已存在，请指定新输出路径")
    cases = [case for case in load_evaluation_cases(args.questions) if case.split == args.split]
    if not cases:
        parser.error("没有 development 问题")
    if args.require_reviewed and any(case.review_status != "human_verified" for case in cases):
        parser.error("仍有问题未完成人工复核")
    validate_evidence(cases, args.documents)
    options = {
        "chunk_size": args.chunk_size,
        "chunk_overlap": args.chunk_overlap,
        "chunking_strategy": args.chunking_strategy,
        "context_char_budget": args.context_char_budget,
        "context_policy": args.context_policy,
        "llm_provider": "extractive",
    }
    if not args.configured_embeddings:
        options["embedding_provider"] = "hashing"
    settings = Settings(**options)
    settings.validate()
    chunks, sources = load_corpus(
        args.documents, TextChunker(args.chunk_size, args.chunk_overlap,
                                   strategy=args.chunking_strategy)
    )
    validate_relevant_sources(cases, sources)
    searchers = build_searchers(chunks, settings, args.strategies)
    diagnostics = diagnose_retrieval(
        cases, chunks, searchers, top_k=args.top_k, char_budget=args.context_char_budget,
        context_policy=args.context_policy,
    )
    report = build_report(
        settings=settings, documents_dir=args.documents, questions_path=args.questions,
        document_sources=sources, chunks=chunks, cases=cases, top_ks=[args.top_k], results={},
        chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap,
    )
    del report["results"]
    report["schema_version"] = "retrieval-diagnostics/1.0"
    report["configuration"].update({"diagnostic_pool_k": POOL_K, "strategies": list(searchers)})
    report["provenance"]["template_sha256"] = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    report["diagnostics"] = diagnostics
    report["outcome_labels"] = OUTCOMES
    report["warnings"].extend([
        "诊断只使用开发划分；跳过不可回答和缺少证据标注的问题，不衡量拒答或澄清质量。",
        "阶段标签描述标注字符范围的覆盖现象，不证明语义根因或答案正确性。",
        "字符范围基于规范化原文，采用 Python Unicode 字符计数及左闭右开区间。",
        "无预算扩展仅用于定位预算损失；不参与服务回答，不代表等预算改进。",
        "诊断池为各策略最终前 50；Hybrid 两路各取 50 后融合。池外不等于两路原始召回均缺失。",
    ])
    if settings.embedding_provider == "hashing":
        report["warnings"].append("Dense/Hybrid 使用 Hashing 演示向量，不代表真实语义模型效果。")
    paths = write_artifacts(report, args.output)
    print(json.dumps({"outputs": [str(path) for path in paths],
                      "case_status_counts": diagnostics["case_status_counts"],
                      "strategies": diagnostics["strategies"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
