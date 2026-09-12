"""Export evidence and draft answers for an actual human review, without changing labels."""

import json
from pathlib import Path


def main() -> None:
    path = Path("evaluation/questions.support.candidate.jsonl")
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    lines = [
        "# 技术支持评测候选人工复核表",
        "",
        "所有条目均为待复核。勾选本表不会自动将数据标记为已验证。",
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
    Path("evaluation/SUPPORT_REVIEW.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Exported {len(rows)} candidate rows to evaluation/SUPPORT_REVIEW.md")


if __name__ == "__main__":
    main()
