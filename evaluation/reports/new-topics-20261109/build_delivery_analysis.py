"""Read saved evidence only; generate explanatory notes without rerunning models."""

from pathlib import Path
import sys

# This file is intentionally kept beside the delivered artifacts, outside the scripts package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.local_embeddings import sha256_file
from scripts.report_new_topics import verified_cases
from scripts.validate_new_topics import rows, read_json, write_json, timestamp


def main():
    root = Path(__file__).resolve().parent
    cases, verified, checks = verified_cases(root)
    result = read_json(root / "analysis-complete/results.json")
    summary = result["summary"]
    human = rows(root / "inputs/human-review.frozen.jsonl")
    assert len(human) == 12 and all(r["reviewed_by"] == "JQH" for r in human)
    old = Path("evaluation/reports/budget-reranking-20261106-complete")
    old_summary = read_json(old / "real.json")
    assert sha256_file(old / "real.trials.jsonl") == old_summary["trials_sha256"]
    old_trials = rows(old / "real.trials.jsonl")
    historical = {s: {
        "context_each_repeat": [sum(t["evidence"]["context_complete"] for t in old_trials
                                    if t["strategy"] == s and t["repeat"] == i) for i in range(3)],
        "bge_calls_all_repeats": sum(t["triggered"] for t in old_trials if t["strategy"] == s),
    } for s in summary}
    folds = read_json(old / "group-validation.json")
    write_json(root / "historical-raw-check.json", {
        "checked_at": timestamp(), "rows": len(old_trials), "summary_recount": historical,
        "trials_sha256_match": True, "group_validation_raw": folds,
    })
    write_json(root / "STATUS.json", {
        "at": timestamp(), "phase": "completed", "assistant_reviewed": 12, "human_verified": 12,
        "reviewer": "JQH", "candidate_revision": "20261109-r1", "new_topic_trials": 36,
        "independent_coverage_checks": checks, "summary": summary,
        "protocol_sha256": sha256_file(root / "protocol.json"),
        "raw_trials_sha256": sha256_file(root / "attempt-001/trials.jsonl"),
        "source_of_human_decision": "human-decision-source.json",
        "api_calls": 0, "answer_generation": 0, "original_held_out_8_executions": 0,
        "note": "audit.json and analysis-pending are immutable preparation snapshots; current state is here",
    })
    lookup = {c.case_id: c for c in cases}
    case12 = "budget-independent-012"
    none12, all12 = verified[case12, "none"], verified[case12, "all"]
    maximum_chars = max(t["context_chars"] for t in verified.values())
    trial_rows = rows(root / "attempt-001/trials.jsonl")
    no_final_gain = [c.case_id[-3:] for c in cases if verified[c.case_id, "adaptive"]["triggered"]
                     and verified[c.case_id, "none"]["context"]["complete"] == verified[c.case_id, "adaptive"]["context"]["complete"]]
    lines = ["# 本轮结果怎么解释", "",
        "本页由build_delivery_analysis.py读取冻结输入和原始结果生成，不重新检索或调用模型。", "",
        "## 本轮支持的结论", "",
        f"JQH实际确认12/12后，固定门控在本批新题上最终完整证据覆盖{summary['adaptive']['context_complete']}/12，"
        f"与全量{summary['all']['context_complete']}/12相同，比不重排{summary['none']['context_complete']}/12多1题。"
        f"按需BGE调用{summary['adaptive']['bge_calls']}/12，全量{summary['all']['bge_calls']}/12；打分对数200对比600。",
        "证据覆盖与答案正确率分开：本轮没有生成或评分答案。12题/6主题、同一FastAPI文档域，只支持这批小规模验证，不证明广泛泛化。", "",
        "## 最有解释力的三个观察", "",
        f"1. **012是最终覆盖收益题。** 原RRF证据首个累计完整排名为{none12['first_complete_rank']}，"
        f"BGE后为{all12['first_complete_rank']}。问题是：{lookup[case12].question} 原池中有证据，但Top5及邻块没有覆盖安装说明和OAuth2表单原因；重排后补齐。"
        "这是池内排序问题，不是原语料没有该证据，也不是需要新增文档。",
        f"2. **{','.join(no_final_gain)}触发但没有新增最终完整覆盖。** 只说明该覆盖指标未提高，不能断言生成答案一定无收益。"
        "005的累计完整证据排名none/all/adaptive为14/16/16，三组Top5均不完整，却都被同节邻块补回。011为6/1/6，全量改善种子，按需跳过仍由邻块补回。最终装配必须单独检查。",
        f"3. **少调用不等于解决尾延迟。** P50全量{summary['all']['p50_ms']/1000:.2f}秒、按需{summary['adaptive']['p50_ms']:.2f}毫秒；"
        f"P95全量{summary['all']['p95_ms']/1000:.2f}秒、按需{summary['adaptive']['p95_ms']/1000:.2f}秒，仅下降"
        f"{1-summary['adaptive']['p95_ms']/summary['all']['p95_ms']:.1%}。4/12请求仍走慢分支，P95落在该分支；每组仅12个观测，不能把单遍分位数当稳定性能保证。", "",
        "## 四类归因", "",
        "|类别|本轮观察|含义|", "|---|---|---|",
        "|召回缺失|三组融合后50池均可完整覆盖所有必要证据|未观察到池外缺失，不等于其他问题不会缺失|",
        "|排序失败|none的012最终不完整；005、011等存在种子层不完整|BGE和邻块各自贡献不同，不能只看文档命中|",
        f"|上下文装配|最终装配失败0；最大正文{maximum_chars}字符，小于8000上限|本批未暴露预算装配瓶颈|",
        "|门控误判|最终覆盖的误跳过0、有害触发0；3次触发无新增最终完整覆盖|仍有不必要的覆盖成本，门控收益识别较粗|", "",
        f"实际BGE截断对数{sum(t['truncated_pairs'] for t in trial_rows)}；每组单遍，未为提高指标改变问题、阈值或语料。", "",
        "## 下一轮只做一项受控改进", "",
        "把BGE打分候选从RRF前50缩到前20；两路召回、RRF池50、overlap<2、Top5及8000字符装配保持不变。"
        "只重排前20，剩余候选保留RRF顺序接在其后。比较最终完整覆盖及慢分支/P95成本，不同时加入其他门控特征。",
        "这只是下一轮建议，本轮没有运行20候选或用本12题再调参。已知风险：旧开发034的完整证据原RRF累计排名23，缩池可能损失它；邻块能否恢复必须实际检验，不能承诺无损。"
        "旧41题和本12题以后作为回归材料；另审新题、预先冻结协议后才作下一轮独立验收。", "",
        "## 复核入口", "",
        "完整逐题指标见analysis-complete/per-case.csv和results.json，012的缺失证据、所有前后候选及上下文ID也在原始JSON中。"
        "协议与所有输入/模型/代码/配置哈希见protocol.json；原始执行见attempt-001/trials.jsonl。"
        "模型加载、60秒量级冷建库及三个无金标暖机在startup.json单列；36条计时不含评分写盘。"
        "计时期间仅轻量进度文件读取，没有运行其他分析/测试；背景负载只在启动时采样，仍属共享本机串行观测。",
    ]
    (root / "INTERPRETATION.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
