"""Export evidence and draft answers for an actual human review, without changing labels."""

import json
import argparse
from pathlib import Path

from app.evaluation import load_evaluation_cases
from scripts.evaluate import validate_evidence, validate_relevant_sources
from app.corpus import read_documents


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions", type=Path, default=Path("evaluation/questions.support.candidate.jsonl")
    )
    parser.add_argument("--split", choices=["development", "test"])
    parser.add_argument("--output", type=Path, default=Path("evaluation/SUPPORT_REVIEW.md"))
    parser.add_argument("--jsonl-output", type=Path, help="另存可编辑的候选副本，拒绝覆盖已有文件")
    parser.add_argument("--documents", type=Path, default=Path("evaluation/support_corpus"))
    parser.add_argument(
        "--validate-only", action="store_true", help="只检查标注、分组及证据原文，不调用模型"
    )
    parser.add_argument("--require-reviewed", action="store_true")
    args = parser.parse_args()
    path = args.questions
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if args.split:
        rows = [row for row in rows if row.get("split") == args.split]
    cases = load_evaluation_cases(path)
    if args.split:
        cases = [case for case in cases if case.split == args.split]
    if not cases:
        raise ValueError("所选划分没有问题")
    validate_evidence(cases, args.documents)
    validate_relevant_sources(cases, [source for source, _, _ in read_documents(args.documents)])
    if args.require_reviewed and any(case.review_status != "human_verified" for case in cases):
        raise ValueError("仍有问题未完成人工复核")
    if args.validate_only:
        count = sum(case.review_status == "human_verified" for case in cases)
        print(f"结构与原文检查通过：{len(cases)} 题，标记人工复核 {count} 题；不证明标注语义正确。")
        return
    if args.jsonl_output:
        if args.jsonl_output.resolve() == args.output.resolve():
            raise ValueError("Markdown 和 JSONL 输出路径不能相同")
        args.jsonl_output.parent.mkdir(parents=True, exist_ok=True)
        with args.jsonl_output.open("x", encoding="utf-8") as stream:
            for row in rows:
                row.setdefault("reviewed_by", "")
                row.setdefault("reviewed_at", "")
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    lines = [
        "# 技术支持评测候选人工复核表",
        "",
        "审核状态以 JSONL 为准。勾选本表不会自动将数据标记为已验证。",
        "",
        "请检查问题是否明确、版本是否适用、答案是否完整、证据是否足够，以及不可回答问题是否确实超出当前语料。",
        "确认后另存 questions.support.reviewed.jsonl，记录 review_status=human_verified、reviewed_by 和 reviewed_at；保留 group_id 和 split。",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                f"## {row['id']} · {row['split']} · {row['category']}",
                "",
                f"- [ ] 人工确认：{row['question']}",
                f"- 范围：`{json.dumps(row['filters'], ensure_ascii=False)}`",
                f"- 预期行为：{row['expected_status']}",
                f"- 参考答案草稿：{row['reference_answer']}",
                "",
            ]
        )
        for anchor in row["evidence"]:
            lines.extend(
                [
                    f"来源：`{anchor['source']}`，规范化原文字符 [{anchor['start_char']}, {anchor['end_char']})",
                    "",
                    "> " + str(anchor["quote"]).replace("\n", "\n> "),
                    "",
                ]
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Exported {len(rows)} candidate rows to {args.output}")


if __name__ == "__main__":
    main()
