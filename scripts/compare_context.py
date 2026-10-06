"""Compare whole-window vs merged context with a shared retrieval result for every pair."""

import argparse
import json
from pathlib import Path

from app.chunking import TextChunker
from app.config import Settings
from app.context_evaluation import compare_context_policies
from app.evaluation import load_evaluation_cases
from scripts.evaluate import (
    build_report, build_searchers, load_corpus, validate_evidence, validate_relevant_sources,
)


def render_markdown(report: dict) -> str:
    c = report["configuration"]
    lines = ["# 上下文合并配对开发实验", "",
             "只描述标注证据覆盖及正文字符消耗，不代表真实模型回答质量。", "",
             f"- Embedding：{c['embedding_provider']} / {c['embedding_model']}",
             f"- 分块：{c['chunking_strategy']} / {c['chunk_size']} / overlap {c['chunk_overlap']}",
             f"- 检索：Top-{c['top_ks'][0]}；每题每策略仅检索一次，所有配对复用同一结果。",
             f"- 题目状态：{json.dumps(report['comparison']['case_status_counts'], ensure_ascii=False)}",
             "- 基线：neighbors；候选：merged_neighbors。相同字符上限不等于实际字符或 token 相等。",
             "", "| 检索 | 预算上限 | 基线完整题 | 合并完整题 | 受益 / 退化 | 基线/合并正文字符合计 | 基线/合并重复字符合计 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for strategy, by_budget in report["comparison"]["results"].items():
        for budget, result in by_budget.items():
            s = result["summary"]
            old, new = [s["policies"][p] for p in ("neighbors", "merged_neighbors")]
            pair = s["paired"]
            lines.append(
                f"| {strategy} | {budget} | {old['complete_case_count']}/{s['evaluated_case_count']} | "
                f"{new['complete_case_count']}/{s['evaluated_case_count']} | "
                f"{len(pair['gained_case_ids'])} / {len(pair['lost_case_ids'])} | "
                f"{old['context_chars_total']} / {new['context_chars_total']} | "
                f"{old['duplicate_chars_total']} / {new['duplicate_chars_total']} |"
            )
    lines.extend(["", "## 逐题变化（完整保留受益与退化）", ""])
    for strategy, by_budget in report["comparison"]["results"].items():
        for budget, result in by_budget.items():
            for row in result["details"]:
                if row["transition"] == "unchanged":
                    continue
                case = report["comparison"]["cases"][row["id"]]
                old, new = [row["policies"][p] for p in ("neighbors", "merged_neighbors")]
                lines.append(
                    f"- {strategy} / {budget} / {row['id']} / {row['transition']}："
                    f"证据 Recall {old['evidence_recall']:.4f} → {new['evidence_recall']:.4f}；"
                    f"字符 {old['context_chars']} → {new['context_chars']}。{case['question']}"
                )
    lines.extend(["", "## 使用边界", "", *["- " + warning for warning in report["warnings"]],
                  "", "## 复现指纹", ""])
    lines.extend(f"- {key}: `{value}`" for key, value in report["provenance"].items()
                 if key.endswith("sha256"))
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, default=Path("evaluation/support_corpus"))
    parser.add_argument("--questions", type=Path,
                        default=Path("evaluation/questions.support.candidate.jsonl"))
    parser.add_argument("--split", choices=["development"], default="development")
    parser.add_argument("--budgets", type=int, nargs="+", default=[1200, 2400, 4800])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--strategies", nargs="+", choices=["bm25", "dense", "hybrid"],
                        default=["bm25", "dense", "hybrid"])
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--chunk-overlap", type=int, default=80)
    parser.add_argument("--chunking-strategy", choices=["window", "sections"], default="sections")
    parser.add_argument("--require-reviewed", action="store_true")
    parser.add_argument("--configured-embeddings", action="store_true",
                        help="显式使用已配置的 Embedding，可能产生接口请求；默认强制 Hashing")
    parser.add_argument("--output", type=Path, default=Path("work/context-comparison/development.json"))
    args = parser.parse_args()
    if not 1 <= args.top_k <= 50 or any(b < 1 for b in args.budgets):
        parser.error("top-k 范围 1..50，字符预算必须大于 0")
    if args.output.suffix.lower() != ".json":
        parser.error("输出必须以 .json 结尾")
    markdown_path = args.output.with_suffix(".md")
    if args.output.exists() or markdown_path.exists():
        parser.error("报告已存在，请换用新文件名")
    cases = [case for case in load_evaluation_cases(args.questions) if case.split == args.split]
    if not cases or (args.require_reviewed and any(c.review_status != "human_verified" for c in cases)):
        parser.error("开发集为空或未完成人工复核")
    validate_evidence(cases, args.documents)
    options = dict(chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap,
                   chunking_strategy=args.chunking_strategy, llm_provider="extractive",
                   context_policy="neighbors", context_char_budget=max(args.budgets))
    if not args.configured_embeddings:
        options["embedding_provider"] = "hashing"
    settings = Settings(**options)
    settings.validate()
    chunks, sources = load_corpus(args.documents, TextChunker(
        args.chunk_size, args.chunk_overlap, strategy=args.chunking_strategy
    ))
    validate_relevant_sources(cases, sources)
    comparison = compare_context_policies(
        cases, chunks, build_searchers(chunks, settings, args.strategies),
        top_k=args.top_k, budgets=args.budgets,
    )
    report = build_report(
        settings=settings, documents_dir=args.documents, questions_path=args.questions,
        document_sources=sources, chunks=chunks, cases=cases, top_ks=[args.top_k], results={},
        chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap,
    )
    del report["results"]
    report["schema_version"] = "context-comparison/1.0"
    # No singular budget/policy for a paired matrix.
    report["configuration"].pop("context_char_budget")
    report["configuration"].pop("context_policy")
    report["configuration"].update(
        context_budgets=sorted(set(args.budgets)), policies=["neighbors", "merged_neighbors"],
        strategies=list(comparison["results"]),
    )
    report["comparison"] = comparison
    report["answer_correctness"] = None
    report["warnings"].extend([
        "仅开发集、仅证据范围诊断；未经人工核验的标注不可作为正式效果依据。",
        "每个配对共享初始检索和预算上限；实际正文字符、提示长度和 token 不保证相等。",
        "贪心合并会改变预算装配顺序，可能出现退化；保留全部变化，不自动更换默认策略。",
        "未调用生成模型，也没有测量模型答案质量、API 延迟或账单费用。",
    ])
    if settings.embedding_provider == "hashing":
        report["warnings"].append("Dense/Hybrid 使用 Hashing 演示向量，不代表语义模型效果。")
    raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    markdown = render_markdown(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(raw)
    with markdown_path.open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    print(markdown)
    print(f"JSON: {args.output}\nMarkdown: {markdown_path}")


if __name__ == "__main__":
    main()
