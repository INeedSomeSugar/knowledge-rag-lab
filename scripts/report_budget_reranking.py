"""Independently audit raw retrieval trials and derive the experiment handoff."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import mean, median, quantiles

from app.chunking import TextChunker
from app.evaluation import load_evaluation_cases
from app.local_embeddings import sha256_file
from scripts.evaluate import load_corpus
from scripts.experiment_budget_reranking import read_json, verify_hashes, write_json

NAMES = ("none", "all", "adaptive")
LABELS = {"none": "不重排", "all": "全部重排", "adaptive": "按需重排"}


def change_label(reduction):
    return f"减少 {reduction:.1%}" if reduction >= 0 else f"增加 {-reduction:.1%}"


def covered(anchor, hits, chunks):
    """Independent cursor implementation; does not call the evaluation coverage helper."""
    intervals = sorted(
        (chunks[i].start_char, chunks[i].end_char)
        for i in hits
        if chunks[i].source == anchor["source"]
    )
    cursor = anchor["start_char"]
    for left, right in intervals:
        if right <= cursor:
            continue
        if left > cursor:
            return False
        cursor = right
        if cursor >= anchor["end_char"]:
            return True
    return False


def complete(case, ids, chunks):
    return all(covered(anchor, ids, chunks) for anchor in case.evidence)


def first_rank(case, ids, chunks):
    return next((i for i in range(1, len(ids) + 1) if complete(case, ids[:i], chunks)), None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--experiment-dir", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    if args.output_dir.exists():
        p.error("derived output must be new; raw results never change")
    root = args.experiment_dir
    real, replay, folds, selection = [
        read_json(root / name)
        for name in ("real.json", "replay.json", "group-validation.json", "selection.json")
    ]
    config = selection["configuration"]
    verify_hashes(selection["lock"])
    protection = verify_hashes(read_json(args.run_dir / "protected-before.json"))
    assert sha256_file(root / "real.trials.jsonl") == real["trials_sha256"]
    assert sha256_file(root / "selection.json") == real["selection_sha256"]
    cases = {
        c.case_id: c for c in load_evaluation_cases(Path(config["questions"])) if c.should_answer
    }
    chunks_list, _ = load_corpus(
        Path(config["documents"]), TextChunker(500, 80, strategy="sections")
    )
    chunks = {c.id: c for c in chunks_list}
    trials = [
        json.loads(line)
        for line in (root / "real.trials.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    expected = {(q, n, r) for q in cases for n in NAMES for r in range(config["repeats"])}
    observed = [(r["id"], r["strategy"], r["repeat"]) for r in trials]
    assert len(observed) == len(set(observed)) and set(observed) == expected
    for q in cases:
        for name in NAMES:
            repeated = [r for r in trials if r["id"] == q and r["strategy"] == name]
            assert len({tuple(h["chunk_id"] for h in r["after"]) for r in repeated}) == 1
            assert (
                len(
                    {
                        (r["evidence"]["top_complete"], r["evidence"]["context_complete"])
                        for r in repeated
                    }
                )
                == 1
            )
    checked_scores = 0
    for row in trials:
        case = cases[row["id"]]
        before = [h["chunk_id"] for h in row["before"]]
        after = [h["chunk_id"] for h in row["after"]]
        assert len(before) == len(set(before)) and set(before) == set(after)
        assert all(
            chunks[i].metadata[k] == v
            for i in [*before, *row["context_ids"]]
            for k, v in case.filters.items()
        )
        score = row["evidence"]
        assert complete(case, after[: config["top_k"]], chunks) == score["top_complete"]
        assert complete(case, row["context_ids"], chunks) == score["context_complete"]
        assert sum(len(chunks[i].text) for i in row["context_ids"]) == score["context_chars"]
        assert score["context_chars"] <= config["context_char_budget"]
        overlap = sum(
            h["dense_rank"] is not None
            and h["dense_rank"] <= 5
            and h["sparse_rank"] is not None
            and h["sparse_rank"] <= 5
            for h in row["before"]
        )
        assert overlap == row["signals"]["overlap_count"]
        assert row["triggered"] == (
            row["strategy"] == "all"
            or (
                row["strategy"] == "adaptive"
                and overlap < selection["selected"]["policy"]["threshold"]
            )
        )
        for hit in row["before"]:
            score_rrf = sum(
                1 / (60 + r) for r in (hit["dense_rank"], hit["sparse_rank"]) if r is not None
            )
            assert math.isclose(score_rrf, hit["score"], rel_tol=1e-12)
            assert hit["rerank_score"] is None
        if row["triggered"]:
            assert row["scored_pairs"] == len(before)
            assert all(h["rerank_score"] is not None for h in row["after"])
        else:
            assert before == after and row["scored_pairs"] == 0 and row["rerank_ms"] == 0
            assert all(h["rerank_score"] is None for h in row["after"])
        assert row["total_ms"] >= row["retrieval_ms"] + row["context_ms"]
        checked_scores += 2
    timing = {}
    for name in NAMES:
        subset = [r for r in trials if r["strategy"] == name]
        values = [r["total_ms"] for r in subset]
        p50, p95 = median(values), quantiles(values, n=100, method="inclusive")[94]
        assert math.isclose(p50, real["timing"][name]["timings"]["total_ms"]["p50"], rel_tol=1e-12)
        assert math.isclose(p95, real["timing"][name]["timings"]["total_ms"]["p95"], rel_tol=1e-12)
        timing[name] = {
            "n": len(subset),
            "p50_ms": p50,
            "p95_ms": p95,
            "mean_ms": mean(values),
            "actual_calls": sum(r["triggered"] for r in subset),
            "scored_pairs": sum(r["scored_pairs"] for r in subset),
        }
        first = [r for r in subset if r["repeat"] == 0]
        assert (
            sum(r["evidence"]["top_complete"] for r in first)
            == real["summary"][name]["top_complete"]
        )
        assert (
            sum(r["evidence"]["context_complete"] for r in first)
            == real["summary"][name]["context_complete"]
        )
    sample = {(r["id"], r["strategy"]): r for r in trials if r["repeat"] == 0}
    errors, rows, useful = [], [], []
    for q, case in cases.items():
        group = {n: sample[q, n] for n in NAMES}
        r = {
            "id": q,
            "group_id": case.group_id,
            "question": case.question,
            "filters": case.filters,
            "signals": group["adaptive"]["signals"],
            "triggered": group["adaptive"]["triggered"],
            "strategies": {
                n: {
                    "top_complete": g["evidence"]["top_complete"],
                    "context_complete": g["evidence"]["context_complete"],
                    "first_complete_rank": first_rank(
                        case, [h["chunk_id"] for h in g["after"]], chunks
                    ),
                }
                for n, g in group.items()
            },
        }
        if (
            not r["strategies"]["none"]["context_complete"]
            and r["strategies"]["all"]["context_complete"]
        ):
            useful.append(q)
        outcomes = {(s["top_complete"], s["context_complete"]) for s in r["strategies"].values()}
        if len(outcomes) > 1 or not r["strategies"]["adaptive"]["context_complete"]:
            r["gold_evidence"] = list(case.evidence)
            r["actual_contexts"] = {
                n: [chunks[i].to_dict() for i in g["context_ids"]] for n, g in group.items()
            }
            r["classification"] = (
                "gain_or_regression"
                if len(outcomes) > 1
                else "outside_rrf_pool"
                if r["strategies"]["all"]["first_complete_rank"] is None
                else "below_top5"
            )
            errors.append(r)
        rows.append(r)
    n = len(cases)
    saving = 1 - timing["adaptive"]["actual_calls"] / timing["all"]["actual_calls"]
    reduction = {
        metric: 1 - timing["adaptive"][metric] / timing["all"][metric]
        for metric in ("p50_ms", "p95_ms", "mean_ms")
    }
    recovery_path = args.run_dir / "recovery.json"
    recovery = read_json(recovery_path) if recovery_path.exists() else None
    segment_timing = {}
    for segment in sorted({r.get("measurement_segment", 0) for r in trials}):
        segment_timing[str(segment)] = {}
        for name in NAMES:
            values = [
                r["total_ms"]
                for r in trials
                if r["strategy"] == name and r.get("measurement_segment", 0) == segment
            ]
            segment_timing[str(segment)][name] = {
                "n": len(values),
                "p50_ms": median(values),
                "p95_ms": quantiles(values, n=100, method="inclusive")[94],
            }
    if recovery and recovery.get("mode") == "immutable_prefix_plus_missing_keys":
        prefix = args.run_dir / "prefix.original.jsonl"
        assert sha256_file(prefix) == recovery["partial_sha256"]
        original_rows = [json.loads(s) for s in prefix.read_text(encoding="utf-8").splitlines()]
        assert len(original_rows) == recovery["partial_trials"]
        for saved, merged in zip(original_rows, trials, strict=False):
            assert saved == {k: v for k, v in merged.items() if k != "measurement_segment"}
    analysis = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "n": n,
        "scope": "development retrieval evidence only; selected on these cases",
        "summary": real["summary"],
        "timing": timing,
        "call_saving_rate": saving,
        "latency_reduction_vs_all": reduction,
        "queries_with_context_gain_if_reranked": useful,
        "adaptive_calls_with_no_context_gain": [
            q for q in cases if sample[q, "adaptive"]["triggered"] and q not in useful
        ],
        "validation": {
            "scope": "Ready within reviewed scope; Share with caveats",
            "independent_coverage_checks": checked_scores,
            "trial_matrix_complete": True,
            "protected_inputs": protection,
            "timing_independently_recomputed": True,
            "human_answer_quality_evaluated": False,
        },
        "inputs_sha256": {
            name: sha256_file(root / name)
            for name in (
                "real.json",
                "real.trials.jsonl",
                "selection.json",
                "replay.json",
                "group-validation.json",
            )
        },
        "generator_sha256": sha256_file(Path(__file__)),
        "measurement_recovery": recovery,
        "segment_timing": segment_timing,
    }
    args.output_dir.mkdir(parents=True)
    write_json(args.output_dir / "analysis.json", analysis)
    write_json(args.output_dir / "errors.json", errors)
    write_json(args.output_dir / "per-query.json", rows)
    lines = [
        "# 预算感知的按需重排：真实本地执行结果",
        "",
        f"在 {n} 道已审核开发题上，按需策略触发 {real['summary']['adaptive']['triggered_queries']}/{n} 次重排，"
        f"最终完整证据覆盖 {real['summary']['adaptive']['context_complete']}/{n}"
        f"（全量重排 {real['summary']['all']['context_complete']}/{n}），"
        f"BGE 调用与打分对数减少 {saving:.1%}。这是调参开发集上的检索结论。",
        "",
        f"按主题分组的 5 折内部验证仅 {folds['context_complete']}/{n}，未保留两道覆盖增益。"
        "当前规则可作为成本实验结果，尚不足以宣称在未见问题上保持覆盖；服务默认配置保持原值。",
        "",
        "## 三组真实对照",
        "",
        "固定 102 份文档 / 2,212 分块；E5+BM25 等权 RRF，50 候选，Top-5，neighbors / 8,000 字符。",
        "47 道开发题中 41 道可回答题参与，6 道澄清/拒答题单列跳过。覆盖指所有审核证据字符区间均完整纳入。",
        "",
        "| 策略 | Top-5 完整覆盖 | 最终上下文完整覆盖 | 重排触发率 | 真实 P50 | 真实 P95 | 真实均值 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in NAMES:
        s, t = real["summary"][name], timing[name]
        lines.append(
            f"| {LABELS[name]} | {s['top_complete']}/{n} | {s['context_complete']}/{n} | "
            f"{s['triggered_queries']}/{n} ({s['triggered_queries'] / n:.1%}) | "
            f"{t['p50_ms']:.2f} ms | {t['p95_ms']:.2f} ms | {t['mean_ms']:.2f} ms |"
        )
    lines += [
        "",
        "![三组覆盖、调用与延迟](summary.png)",
        "",
        f"相对全部重排，P50 {change_label(reduction['p50_ms'])}、P95 {change_label(reduction['p95_ms'])}、"
        f"均值{change_label(reduction['mean_ms'])}。调用减少不能直接等同于延迟同比减少；"
        "按需策略仍有约四成慢请求，因此尾延迟的节约通常有限。",
        "",
        "## 阈值选择：缓存回放，不能作为真实计时",
        "",
        "执行前方案见 `evaluation/budget_reranking/plan-20261106.md`；14 个单特征阈值均在下表。"
        "选择标准是保留 all 的每道最终完整题且不损失 none 的最终完整题，Top-5 数量不低于 none，再取最少调用。",
        "",
        "| 特征 | 阈值 | 触发/41 | Top-5 | 最终上下文 | 相对全量最终损失 | 满足主约束 |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for r in replay["grid"]:
        lines.append(
            f"| {r['policy']['family']} | {r['policy']['threshold']} | {r['trigger_count']} | "
            f"{r['top_complete']} | {r['context_complete']} | {', '.join(r['lost_vs_all']) or '无'} | "
            f"{'是' if r['preserves_all'] else '否'} |"
        )
    lines += [
        "",
        "`overlap` 的条件为共同 Top-5 分块数 < 阈值；`margin` 为 (RRF第1分−第5分)/第1分 <= 阈值。"
        "冻结规则为 overlap < 2。候选的两路排名和 RRF 分在重排前已有；"
        "运行门控不接收题号、金标、参考答案或 BGE 分。开发标签只用于离线阈值选择。",
        "最少调用只针对这 14 个预注册规则；margin 网格在 0.05 与 1.01 之间没有中间点，"
        "未试探更细阈值，因此不能据此断言 margin 信号无效或当前规则全局最优。",
        "",
        "![阈值与预算](frontier.png)",
        "",
        "| 调用预算上限 | 选中规则 | 实际触发 | 最终完整覆盖 | Top-5 |",
        "|---|---|---:|---:|---:|",
    ]
    for f in replay["frontier"]:
        s = f["selected"]
        lines.append(
            f"| {f['budget_rate']:.0%} | {s['policy']} | {s['trigger_count']}/{n} | "
            f"{s['context_complete']}/{n} | {s['top_complete']}/{n} |"
            if s
            else f"| {f['budget_rate']:.0%} | 无可行规则 | — | — | — |"
        )
    lines += [
        "",
        "25% 预算未能保留全部收益；50% 预算下所选规则满足开发集质量约束。"
        "预算是开发分布上的调用比例，并非任意线上流量的硬性配额或逐请求截止时间。",
        "",
        "## 分组内部验证与适配偏差",
        "",
        "固定按 group_id 排序后轮转分配 5 折，只在训练折选阈值，在验证折评分。此处仍是开发集回放，"
        "没有真实计时或独立测试。历史开发结果此前已被多次查看，因此本轮也不构成严格盲测。",
        "",
        "| 折 | 验证主题 | 训练选中规则 | 验证题数 | 触发 | Top-5 | 最终上下文 | 相对全量遗漏 |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for f in folds["folds"]:
        groups = sorted({cases[q].group_id for q in f["validation_ids"]})
        v = f["validation"]
        lines.append(
            f"| {f['fold']} | {', '.join(groups)} | {f['selected']['policy']} | {v['n']} | "
            f"{v['trigger_count']} | {v['top_complete']} | {v['context_complete']} | "
            f"{', '.join(v['lost_vs_all']) or '无'} |"
        )
    lines += [
        "",
        f"跨折合计：触发 {folds['trigger_count']}/{n}，Top-5 {folds['top_complete']}/{n}，"
        f"最终 {folds['context_complete']}/{n}。门控从少数收益题学到的阈值不稳定，"
        "低重合也不必然意味着重排有收益；后续应优先补充独立主题与更丰富困难样本，"
        "保持规则固定后验证，而不是继续对这 41 题增加特征和阈值。",
        "本次两道收益题恰好都在验证折 2，该折训练数据没有重排增益正例，最少调用目标因此选择 overlap < 0，"
        "即从不重排。这个缺口直接反映收益正例稀少时选择不可靠，并不证明所有未见问题都没有收益。"
        "未根据该结果重排分组或修改主规则。",
        "",
        "## 收益、成本与错误归因",
        "",
        "完整逐题表见 [CASES.md](CASES.md)，含全部三组覆盖与证据首个完整排名；"
        "带实际上下文和原文范围的错误档案见 [errors.json](errors.json)。",
        "",
        "| 问题 | 触发 | overlap | none/all/adaptive 首个完整候选排名 | none/all/adaptive Top-5 | none/all/adaptive 最终 |",
        "|---|---|---:|---|---|---|",
    ]
    for r in errors:
        s = r["strategies"]
        lines.append(
            f"| {r['id']} | {r['triggered']} | {r['signals']['overlap_count']} | "
            + "/".join(str(s[n]["first_complete_rank"]) for n in NAMES)
            + " | "
            + "/".join(str(int(s[n]["top_complete"])) for n in NAMES)
            + " | "
            + "/".join(str(int(s[n]["context_complete"])) for n in NAMES)
            + " |"
        )
    lines += [
        "",
        "support-029、034 是两道最终覆盖收益题，均被触发；support-009 的全量重排种子退化被门控避开，"
        "全量策略原先能由邻块补回，故三组最终上下文在该题均完整。",
        "",
        "support-006 重排后完整证据仍在第 24 名，013 与 026 在第 6 名，020 的完整证据仍在融合后 50 池外。"
        "门控只决定是否重排，不修复候选池缺失或重排本身的排序失败；本轮不改变候选大小或 Top-K。",
        "",
        f"触发的 {real['summary']['adaptive']['triggered_queries']} 道题中，仅 {len(useful)} 道带来最终完整覆盖增加，"
        f"其余 {len(analysis['adaptive_calls_with_no_context_gain'])} 道仍有重排成本但该指标不变。"
        "因此当前门控有节约，但对重排收益的识别仍粗糙。新增最终覆盖损失以 transitions 原始记录为准：",
        "",
        "```json",
        json.dumps(real["transitions"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 计时边界与复现",
        "",
        f"每组 {config['repeats']} 次重复、共 {timing['none']['n']} 次执行，"
        f"三组合计 {len(trials)} 条原始记录。逐题轮换顺序，每组独立编码查询与检索，"
        "按需组独立执行门控，并只在触发时真正调用 BGE。文档索引在内存中复用；"
        "查询向量与 BGE 结果不缓存。真实总时间包含门控、版本可用分块筛选和上下文装配，"
        "评分及日志写盘不计入。模型加载/文件校验、冷建库与每段各组一次暖机单列，"
        f"共排除 {real['execution']['warmups_excluded']} 次暖机；各段数据在 real.json/measurement_segments。",
        "三组复用驻内存索引和已加载模型；本轮节约的是重排推理调用与打分对数，未证明常驻模型内存减少。",
        "",
        "线性插值 P50/P95 取每组全部执行的经验分布，重复仍只有 41 个独立问题。"
        "这是共享本机 CPU 上的串行暖态观测，文档准备及轻量脚本工作可能共享 CPU；无并发压测或生产 SLA 结论。"
        "本轮 torch 为 CPU 构建，未使用 CUDA，未修改或操作另一项目的实验目录及进程。",
        "",
        f"按需门控 P50/P95：{real['timing']['adaptive']['timings']['gate_ms']['p50']:.4f}/"
        f"{real['timing']['adaptive']['timings']['gate_ms']['p95']:.4f} ms。"
        f"实际计时阶段 BGE 调用：all={timing['all']['actual_calls']}、adaptive={timing['adaptive']['actual_calls']}；"
        f"打分对数 {timing['all']['scored_pairs']}→{timing['adaptive']['scored_pairs']}（不含暖机）。",
        "",
        "![真实延迟经验分布](latency-cdf.png)",
        "",
        f"逐条排序不稳定 {real['execution']['unstable_rankings']}；与冻结回放排序差异 "
        f"{real['execution']['replay_ranking_mismatches']}，覆盖差异 {real['execution']['replay_coverage_mismatches']}，"
        f"门控决策差异 {real['execution']['gate_decision_mismatches']}。",
        "",
        f"独立重算 {checked_scores} 项逐次证据完整性检查、核对三组分位数与试验矩阵，"
        f"{protection['checked']} 份既有文件保护哈希通过。检索结论在此范围可复核；"
        "携带开发集调参和内部验证不稳定的限制使用。",
        "",
        "复现命令见 [REPRODUCE.md](REPRODUCE.md)。逐次记录在上级 `real.trials.jsonl`，"
        "回放阈值、冻结选择和 group-validation 分开存档。",
        "",
        "## 后续独立验证",
        "",
        "已在规则冻结后准备 12 道候选、6 个新文档主题及原文审核包，来自 0.117.1 / 0.118.0。"
        "12 道均为助手拟题、unreviewed；逐字与来源检查为 assistant_checked，真实人工确认 0/12。"
        "未运行新候选检索或答案评分。实际审核需补充语义重复、参考答案充分性与证据完整性检查。"
        "原有 8 道保留测试未读取题文、未调参、未运行。",
        "",
        "下一步是实际人员审核后，保持本轮规则固定进行一次独立验证，优先判断两道开发收益能否扩展到新主题；"
        "若不能，应接受简单信号不足的结论，考虑离线学习收益预测或缩减候选池的独立实验。"
        "对 P95 敏感的场景还需分别验证较小 BGE 候选池或独立设备，本轮没有相关收益结论。",
        "",
        "本轮生成答案 0、付费 API 0、答案正确率未测。不能将以上覆盖或节约数字写成端到端问答准确率。",
        "",
    ]
    repeat_lines = [
        "## 逐重复计时",
        "",
        "| 重复 | 策略 | P50 (ms) | P95 (ms) |",
        "|---:|---|---:|---:|",
    ]
    for repeat in range(config["repeats"]):
        for name in NAMES:
            t = real["timing"][name]["by_repeat"][str(repeat)]
            repeat_lines.append(
                f"| {repeat + 1} | {LABELS[name]} | {t['p50']:.2f} | {t['p95']:.2f} |"
            )
    repeat_lines += [""]
    if recovery and recovery.get("mode") == "immutable_prefix_plus_missing_keys":
        repeat_lines += [
            "## 中断记录与分段测量",
            "",
            f"本表包含第三次执行前缀 {recovery['partial_trials']} 条，以及新进程补齐的 "
            f"{len(trials) - recovery['partial_trials']} 条，合计完整预定矩阵 369 条。"
            "前缀逐字副本和 SHA256 保留并逐条比对，没有按结果剔除或重测。"
            "冻结 selection 哈希相同，没有重新选阈值。",
            "",
            "前两次失败启动（66 条前缀、一次建库中断）另存原目录且排除。第三次退出原因与退出码不可恢复。"
            "按恢复补充仅执行缺失键，重新建库和暖机；冷启动不计入热请求分位数。"
            "第二重复跨两段，个别配对存在中断间隔，观察到的延迟可能受重启与背景负载影响。"
            "各段题目组成也不同，以下分段表用于审计，不用来推断策略间因果性能差异。"
            "恢复段的静态环境沿用源段，available_ram_before/background_cpu_percent_before 未重新采样，"
            "不据此宣称恢复时无后台负载。额外失败启动开销不能算成策略节约。",
            "",
            "| 计时段 | 策略 | 执行数 | P50 (ms) | P95 (ms) |",
            "|---:|---|---:|---:|---:|",
        ]
        for segment, groups in segment_timing.items():
            for name, t in groups.items():
                repeat_lines.append(
                    f"| {int(segment) + 1} | {LABELS[name]} | {t['n']} | "
                    f"{t['p50_ms']:.2f} | {t['p95_ms']:.2f} |"
                )
        repeat_lines += [""]
    elif recovery:
        repeat_lines += [
            "## 中断记录与测量恢复",
            "",
            f"首次计时只保存 {recovery['partial_trials']} 条后进程消失，未生成最终报告；"
            "另一次后台启动在建库阶段结束，也没有查询计时。两批均完整保留，"
            "原进程退出状态和准确中断原因未确认。",
            "",
            "最终表格只使用本目录重新完整执行的 369 条，不拼接中断前缀。"
            "复用的 selection.json 哈希完全相同，恢复没有重新调阈值；新进程重新冷建库和暖机。"
            "中断运行的额外计算不算作策略节约，原始记录可追溯。",
            "",
        ]
    insert_at = lines.index("## 后续独立验证")
    lines[insert_at:insert_at] = repeat_lines
    (args.output_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    case_lines = [
        "# 全部 41 题的配对结果",
        "",
        "顺序均为 none / all / adaptive；排名 None 表示融合后池内未完整覆盖。",
        "",
        "| ID | 主题 | overlap | margin | 触发 | Top-5 | 最终上下文 | 完整证据排名 |",
        "|---|---|---:|---:|---|---|---|---|",
    ]
    for r in rows:
        s = r["strategies"]
        case_lines.append(
            f"| {r['id']} | {r['group_id']} | {r['signals']['overlap_count']} | "
            f"{r['signals']['rrf_margin']:.6f} | {r['triggered']} | "
            + "/".join(str(int(s[n]["top_complete"])) for n in NAMES)
            + " | "
            + "/".join(str(int(s[n]["context_complete"])) for n in NAMES)
            + " | "
            + "/".join(str(s[n]["first_complete_rank"]) for n in NAMES)
            + " |"
        )
    (args.output_dir / "CASES.md").write_text("\n".join(case_lines) + "\n", encoding="utf-8")
    resume = [
        "# 可用简历表述（限定开发检索实验）",
        "",
        f"- 设计基于稀疏/稠密 Top-5 排名分歧的预算感知重排策略，在 102 份版本化文档、2,212 分块与 "
        f"41 道已审核开发题上，将 BGE 触发率降至 {real['summary']['adaptive']['triggered_queries']}/{n}"
        f"（减少 {saving:.1%} 调用），保留全量重排 {real['summary']['adaptive']['context_complete']}/{n} "
        f"最终证据覆盖，Top-5 完整覆盖达到 {real['summary']['adaptive']['top_complete']}/{n}。",
        f"- 建立离线阈值回放与真实本地推理解耦的配对评测流程，完成 14 个规则阈值、按主题 5 折验证及 "
        f"{len(trials)} 次 CPU 检索执行，真实 P50 从 {timing['all']['p50_ms'] / 1000:.2f}s 降至 "
        f"{timing['adaptive']['p50_ms']:.1f}ms，记录 P95 {timing['adaptive']['p95_ms'] / 1000:.2f}s "
        f"与内部验证 {folds['context_complete']}/{n} 的泛化缺口，并准备 12 道待人工审核的独立验证候选。",
        "",
        "上述两条仅描述开发集检索与工程评测，不延伸为答案准确率、独立测试或部署效果。",
        "",
    ]
    (args.output_dir / "RESUME.md").write_text("\n".join(resume), encoding="utf-8")
    print(
        json.dumps(
            {
                "summary": analysis["summary"],
                "call_saving_rate": saving,
                "validation": analysis["validation"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
