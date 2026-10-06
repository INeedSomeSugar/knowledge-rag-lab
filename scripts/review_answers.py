"""Export and score human judgments bound to an immutable paired evaluation report."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path


def report_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def draft_digest(detail: dict) -> str:
    return hashlib.sha256(
        json.dumps(detail, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def load_pairs(report_path: Path) -> tuple[dict, dict, dict]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    comparison = report.get("verification_comparison", {})
    if comparison.get("design") != "same_draft_same_context":
        raise ValueError("需要同一答案的配对核验报告")
    baseline = comparison["without_verification"]["details"]
    checked = report["answers"]["details"]
    before = {item["id"]: item for item in baseline}
    after = {item["id"]: item for item in checked}
    if len(before) != len(baseline) or len(after) != len(checked) or before.keys() != after.keys():
        raise ValueError("报告中问题 ID 重复或配对不完整")
    return report, before, after


def export_review(report_path: Path, output: Path) -> int:
    _, before, _ = load_pairs(report_path)
    digest = report_digest(report_path)
    markdown_path = output.with_suffix(".md")
    if output.exists() or markdown_path.exists() or output == markdown_path:
        raise ValueError("复核输出已存在或路径冲突，请使用新文件名以保留人工标注")
    rows = []
    lines = [
        "# 初始答案人工复核",
        "",
        "逐题检查答案完整性与结论支持关系。在 JSONL 中填写两个布尔判断、审核人、带时区时间和备注。",
        "全部字段初始为待审；不要用另一个模型的评分冒充人工评分。",
        "只导出实际生成的初始答案；不展示核验结论，减少对人工判断的影响。",
        "候选问题本身的审核与答案评分是两个步骤。参考答案未经审核时也可能有误。",
        "",
    ]
    for case_id, detail in before.items():
        response = detail["response"]
        if response["status"] != "answered":
            continue
        rows.append(
            {
                "id": case_id,
                "report_sha256": digest,
                "draft_sha256": draft_digest(detail),
                "review_status": "pending",
                "answer_correct": None,
                "all_claims_supported": None,
                "reviewed_by": "",
                "reviewed_at": "",
                "notes": "",
            }
        )
        lines.extend(
            [
                f"## {case_id}",
                "",
                f"问题：{response['question']}",
                "",
                f"范围：{json.dumps(detail.get('filters', {}), ensure_ascii=False)}",
                "",
                f"问题审核状态：{detail['review_status']}",
                "",
                f"参考答案：{detail['reference_answer']}",
                "",
                "### 待审答案",
                "",
                response["answer"],
                "",
                "### 实际上下文",
                "",
            ]
        )
        for index, hit in enumerate(response["context"], 1):
            lines.extend(
                [
                    f"[{index}] {hit['source']} · 字符 [{hit['start_char']}, {hit['end_char']})",
                    "",
                    "> " + hit["text"].replace("\n", "\n> "),
                    "",
                ]
            )
        lines.extend(["### 标注证据", ""])
        for anchor in detail.get("gold_evidence", []):
            lines.extend([anchor["source"], "", "> " + anchor["quote"].replace("\n", "\n> "), ""])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    with markdown_path.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    return len(rows)


def score_review(report_path: Path, review_path: Path) -> dict:
    report, before, after = load_pairs(report_path)
    digest = report_digest(report_path)
    eligible = {
        key: value for key, value in before.items() if value["response"]["status"] == "answered"
    }
    seen = set()
    reviewed = []
    for line in review_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        case_id = row["id"]
        if case_id not in eligible or case_id in seen:
            raise ValueError("人工复核 ID 未知、重复或不属于初始生成答案")
        seen.add(case_id)
        if row.get("report_sha256") != digest or row.get("draft_sha256") != draft_digest(
            before[case_id]
        ):
            raise ValueError("人工评分与原始报告或答案不匹配，请重新导出复核表")
        if row.get("review_status") == "pending":
            continue
        if row.get("review_status") != "human_verified":
            raise ValueError("审核状态必须为 pending 或 human_verified")
        if any(
            type(row.get(key)) is not bool for key in ("answer_correct", "all_claims_supported")
        ):
            raise ValueError("人工判断必须为布尔值，不能用字符串或缺失值")
        if not isinstance(row.get("reviewed_by"), str) or not row["reviewed_by"].strip():
            raise ValueError("人工评分必须记录审核人")
        try:
            reviewed_at = datetime.fromisoformat(row["reviewed_at"])
            if reviewed_at.utcoffset() is None:
                raise ValueError("missing timezone")
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("审核时间必须为带时区的 ISO 8601 时间") from exc
        reviewed.append(row)

    def ratio(numerator: int, denominator: int) -> dict:
        return {
            "numerator": numerator,
            "denominator": denominator,
            "rate": round(numerator / denominator, 4) if denominator else None,
        }

    def blocked(row: dict) -> bool:
        return after[row["id"]]["response"]["reason"] == "support_verification_failed"

    kept = [row for row in reviewed if after[row["id"]]["response"]["status"] == "answered"]
    correct = [row for row in reviewed if row["answer_correct"]]
    supported_correct = [row for row in correct if row["all_claims_supported"]]
    unsupported = [row for row in reviewed if not row["all_claims_supported"]]
    return {
        "schema_version": "1.0",
        "report_sha256": digest,
        "review_sha256": report_digest(review_path),
        "eligible_draft_count": len(eligible),
        "reviewed_count": len(reviewed),
        "coverage": ratio(len(reviewed), len(eligible)),
        "draft_correctness_on_reviewed": ratio(len(correct), len(reviewed)),
        "kept_correctness_on_reviewed": ratio(
            sum(row["answer_correct"] for row in kept), len(kept)
        ),
        "correct_and_supported_drafts_blocked": ratio(
            sum(blocked(row) for row in supported_correct), len(supported_correct)
        ),
        "unsupported_drafts_blocked": ratio(
            sum(blocked(row) for row in unsupported), len(unsupported)
        ),
        "verification_errors_on_reviewed": sum(
            after[row["id"]]["response"]["status"] == "error" for row in reviewed
        ),
        "source_warnings": report.get("warnings", []),
        "note": (
            "这些比例只覆盖已人工评分的初始生成答案；未回答问题不在分母内，不是全问题正确率。"
            "保留答案正确率上升可能仅来自拒答增加，需同时看覆盖率、错误拦截和核验错误数。"
            "不修改原始实验报告；人工身份与判断真实性依赖实际审核流程。"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "score"])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    if args.action == "export":
        count = export_review(args.report, args.output)
        print(f"导出 {count} 份待人工评分答案：{args.output}")
    else:
        if args.reviews is None:
            parser.error("score 需要 --reviews")
        result = score_review(args.report, args.reviews)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
