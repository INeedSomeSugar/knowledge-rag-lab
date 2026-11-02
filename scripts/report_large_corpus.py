"""Validate the additive corpus experiment and derive a paired scale report (no model calls)."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from app.evaluation import load_evaluation_cases
from scripts.compare_embeddings import STRATEGIES
from scripts.evaluate import validate_evidence


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def diagnosed(report):
    return {r["id"]: r for r in report["diagnostics"]["cases"] if r["status"] == "diagnosed"}


def stage(value):
    return {
        "top_k_evidence_recall": value["top_k_evidence_recall"],
        "context_evidence_recall": value["context_evidence_recall"],
        "outcomes": [a["outcome"] for a in value["anchors"]],
        "first_full_coverage_ranks": [a["first_full_coverage_rank"] for a in value["anchors"]],
        "recovered_by_neighbors": [a["recovered_by_neighbors"] for a in value["anchors"]],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--current", type=Path, default=Path("evaluation/reports/corpus-large-v013.json")
    )
    p.add_argument(
        "--baseline",
        type=Path,
        default=Path(
            "work/human-review/20261028/reviewed-retrieval-20261028T044628Z/semantic-reviewed.json"
        ),
    )
    p.add_argument("--audit", type=Path, default=Path("work/experiments/corpus-large-v013-audit"))
    p.add_argument("--cross-encoder", type=Path)
    p.add_argument(
        "--cross-encoder-baseline",
        type=Path,
        default=Path("evaluation/reports/cross-encoder-development-v012-20261028.json"),
    )
    p.add_argument(
        "--output", type=Path, default=Path("evaluation/reports/corpus-large-v013.analysis.json")
    )
    args = p.parse_args()
    if args.output.exists() or args.output.with_suffix(".md").exists():
        p.error("Derived report must be new")
    current, baseline = read(args.current), read(args.baseline)
    documents = Path(current["configuration"]["documents_dir"])
    manifest = read(documents / "manifest.json")
    for entry in manifest["documents"]:
        content = (documents / entry["source"]).read_bytes()
        assert sha(documents / entry["source"]) == entry["sha256"]
        assert entry["source_revision"] == manifest["revisions"][entry["version"]]
        assert len(entry["upstream_sha256"]) == 64 and entry["license"] == "MIT"
        assert not any(marker in content for marker in (b"{!", b"{*"))
        if entry["acquisition"] == "fetched_from_pinned_raw":
            assert entry["upstream_url"].startswith(
                f"https://raw.githubusercontent.com/fastapi/fastapi/{entry['source_revision']}/"
            )
    assert sha(documents / manifest["license_file"]) == manifest["license_sha256"]
    frozen = Path(manifest["baseline"]["directory"])
    frozen_manifest = read(frozen / "manifest.json")
    before = read(args.audit / "protected-before.json")
    changed = [
        name
        for name, item in before["files"].items()
        if not Path(name).is_file() or sha(Path(name)) != item["sha256"]
    ]
    if changed:
        raise ValueError(f"Protected evidence changed: {changed}")
    copied = {}
    for entry in frozen_manifest["documents"]:
        source = entry["source"]
        assert sha(documents / source) == sha(frozen / source) == entry["sha256"], source
        copied[source] = entry["sha256"]
    cases = load_evaluation_cases(Path(current["configuration"]["questions_path"]))
    assert all(c.review_status == "human_verified" for c in cases)
    validate_evidence(cases, documents)
    old, new = diagnosed(baseline), diagnosed(current)
    assert old.keys() == new.keys() and len(new) == 41
    assert current["provenance"]["questions_sha256"] == baseline["provenance"]["questions_sha256"]
    trials_path = args.current.with_suffix(".trials.jsonl")
    trials = [json.loads(line) for line in trials_path.read_text(encoding="utf-8").splitlines()]
    expected = {
        (case_id, strategy, repeat)
        for case_id in new
        for strategy in STRATEGIES
        for repeat in range(3)
    }
    actual = [(t["id"], t["strategy"], t["repeat"]) for t in trials]
    assert len(actual) == len(set(actual)) == len(expected) == 492 and set(actual) == expected
    assert sha(trials_path) == current["execution"]["trials_sha256"]
    assert all(t["same_ranking_as_first"] for t in trials)
    assert current["dataset"]["document_count"] == len(manifest["documents"]) >= 100
    assert 3 <= len(current["dataset"]["version_counts"]) <= 5
    pairs = {}
    for strategy in STRATEGIES:
        groups = {"improved": [], "regressed": [], "unchanged": []}
        details = []
        for case_id in new:
            a, b = (r[case_id]["strategies"][strategy] for r in (old, new))
            av, bv = (v["context_evidence_recall"] == 1 for v in (a, b))
            label = "improved" if bv > av else "regressed" if bv < av else "unchanged"
            groups[label].append(case_id)
            if label != "unchanged":
                details.append(
                    {"id": case_id, "change": label, "before": stage(a), "after": stage(b)}
                )
        counts = [
            r["diagnostics"]["strategies"][strategy]["context_complete_case_count"]
            for r in (baseline, current)
        ]
        incomplete = [
            {"id": i, **stage(r["strategies"][strategy])}
            for i, r in new.items()
            if r["strategies"][strategy]["context_evidence_recall"] < 1
        ]
        pairs[strategy] = {
            "baseline_complete": counts[0],
            "large_complete": counts[1],
            "delta_percentage_points": (counts[1] - counts[0]) / len(new) * 100,
            **groups,
            "changed_case_stages": details,
            "incomplete_cases": incomplete,
            "neighbor_recovered_cases": [
                i
                for i, r in new.items()
                if r["strategies"][strategy]["top_k_evidence_recall"] < 1
                and r["strategies"][strategy]["context_evidence_recall"] == 1
            ],
        }
    generated_at = datetime.now(timezone.utc)
    process_timing = {
        "workflow_wall_seconds_to_analysis": (
            generated_at - datetime.fromisoformat(before["created_at"])
        ).total_seconds(),
        "final_acquisition_seconds": (
            datetime.fromisoformat(manifest["completed_at"])
            - datetime.fromisoformat(manifest["created_at"])
        ).total_seconds(),
    }
    for name in ("comparison", "cross-encoder"):
        path = args.audit / f"{name}-process.json"
        if path.is_file():
            process_timing[name] = read(path)
    result = {
        "generated_at": generated_at.isoformat(),
        "process_timing": process_timing,
        "evidence_type": "reviewed_development_final_context_complete_evidence_coverage",
        "dataset": current["dataset"],
        "baseline_dataset": baseline["dataset"],
        "skipped": manifest["skipped"],
        "acquisition": {
            "created_at": manifest["created_at"],
            "completed_at": manifest["completed_at"],
            "transport": manifest.get("transport"),
            "network_failures": manifest.get("network_failures", []),
        },
        "comparisons": pairs,
        "timing": current["timing"],
        "resources": current["resources"],
        "execution": current["execution"],
        "validation": {
            "protected_file_count": len(before["files"]),
            "changed_files": changed,
            "copied_old_source_count": len(copied),
            "copied_source_sha256": copied,
            "anchor_case_count": len(new),
            "anchor_count": sum(len(c.evidence) for c in cases),
            "anchors_valid": True,
            "trial_count": len(trials),
            "trial_matrix_complete": True,
            "stable_all_repeats": True,
            "protected_snapshot_sha256": sha(args.audit / "protected-before.json"),
        },
        "inputs": {
            str(path): sha(path)
            for path in (
                args.current,
                args.baseline,
                trials_path,
                documents / "manifest.json",
                Path(__file__),
                Path("scripts/fetch_large_corpus.py"),
                Path("scripts/compare_embeddings.py"),
            )
        },
    }
    ce = read(args.cross_encoder) if args.cross_encoder else None
    if ce:
        ce_rows = diagnosed(ce)
        ce_trials_path = args.cross_encoder.with_suffix(".trials.jsonl")
        ce_trials = [
            json.loads(line) for line in ce_trials_path.read_text(encoding="utf-8").splitlines()
        ]
        assert len(ce_trials) == 41 and {t["id"] for t in ce_trials} == new.keys()
        assert all(t["repeat"] == 0 and t["stable"] for t in ce_trials)
        assert sha(ce_trials_path) == ce["trials_sha256"]
        assert new.keys() == ce_rows.keys()
        assert ce["dataset"]["questions_sha256"] == current["provenance"]["questions_sha256"]
        for case_id in new:
            assert stage(ce_rows[case_id]["strategies"]["e5_hybrid"]) == stage(
                new[case_id]["strategies"]["e5_hybrid"]
            )
        assert ce["dataset"]["corpus_files"] == {
            entry["source"]: entry["sha256"] for entry in manifest["documents"]
        }
        result["cross_encoder"] = {
            "report": str(args.cross_encoder),
            "sha256": sha(args.cross_encoder),
            "summary": ce["diagnostics"]["strategies"],
            "transitions": ce["transitions"],
            "timing": ce["timing"],
            "execution": ce["execution"],
        }
        ce_old = read(args.cross_encoder_baseline)
        assert ce_old["dataset"]["questions_sha256"] == ce["dataset"]["questions_sha256"]
        ce_old_rows = diagnosed(ce_old)
        groups = {"improved": [], "regressed": [], "unchanged": []}
        for case_id in new:
            a = ce_old_rows[case_id]["strategies"]["e5_hybrid_bge"]["context_evidence_recall"]
            b = ce_rows[case_id]["strategies"]["e5_hybrid_bge"]["context_evidence_recall"]
            groups["improved" if b > a else "regressed" if b < a else "unchanged"].append(case_id)
        result["cross_encoder"]["old_corpus_comparison"] = {
            "baseline_report": str(args.cross_encoder_baseline),
            "baseline_sha256": sha(args.cross_encoder_baseline),
            "old_context_complete": ce_old["diagnostics"]["strategies"]["e5_hybrid_bge"][
                "context_complete_case_count"
            ],
            "large_context_complete": ce["diagnostics"]["strategies"]["e5_hybrid_bge"][
                "context_complete_case_count"
            ],
            **groups,
        }
    lines = [
        "# v0.13 扩容语料固定对照与保护校验",
        "",
        "本报告由 scripts.report_large_corpus 生成；仅诊断最终上下文完整证据覆盖，不评价答案正确率。",
        "",
        f"{current['dataset']['document_count']}份文档 / {len(current['dataset']['version_counts'])}版本 / "
        f"{current['dataset']['chunk_count']}分块；版本计数：{json.dumps(current['dataset']['version_counts'])}。",
        f"30个主题×4版本，原34份文件逐字节复制；新增{current['dataset']['document_count'] - 34}份固定提交下载。",
        f"语料指纹：`{current['provenance']['corpus_sha256']}`。",
        "",
        "| 策略 | 旧34份 / 41题 | 新语料 / 41题 | 变化（百分点） | 改善题 | 退化题 |",
        "|---|---:|---:|---:|---|---|",
    ]
    for name, pair in pairs.items():
        a, b = pair["baseline_complete"], pair["large_complete"]
        lines.append(
            f"| {name} | {a}/41 ({a / 41:.2%}) | {b}/41 ({b / 41:.2%}) | "
            f"{pair['delta_percentage_points']:+.2f} | {', '.join(pair['improved']) or '无'} | "
            f"{', '.join(pair['regressed']) or '无'} |"
        )
    lines += ["", "## 跳过清单", ""]
    lines += [f"- {s['version']} / {s['page']}：`{s['reason']}`" for s in manifest["skipped"]] or [
        "无跳过页面。"
    ]
    lines += [
        "",
        "## 阶段诊断",
        "",
        "产品/版本先过滤候选；BM25的IDF与平均长度由整个索引计算。新增版本会影响BM25统计，"
        "新增同版本主题会扩大有效候选池。这是实现层面的影响路径，未另做消融以分离因果。",
        "",
    ]
    for name, pair in pairs.items():
        lines += [f"### {name}", ""]
        for d in pair["changed_case_stages"]:
            lines.append(
                f"- {d['id']} {d['change']}：{json.dumps(d['before'], ensure_ascii=False)} → "
                f"{json.dumps(d['after'], ensure_ascii=False)}"
            )
        lines += [
            f"- 仍不完整：{', '.join(d['id'] for d in pair['incomplete_cases']) or '无'}。",
            f"- 邻块补回：{', '.join(pair['neighbor_recovered_cases']) or '无'}。",
            "",
        ]
    build = current["timing"]["build"]
    peak = current["resources"]["after_trials"]["peak_working_set_bytes"]
    lines += [
        "## 运行与复现",
        "",
        f"从保护快照到派生报告共{process_timing['workflow_wall_seconds_to_analysis']:.2f}秒"
        "（含网络失败、修复、审批与等待）；最终可用语料抓取"
        f"{process_timing['final_acquisition_seconds']:.2f}秒。",
        f"主对照耗时{current['execution']['wall_seconds_before_report_write']:.2f}秒（到报告写入前）；"
        f"E5加载/校验{build['e5_load_and_file_verification_ms'] / 1000:.2f}秒，"
        f"冷建库{build['e5_cold_document_encoding_index_ms'] / 1000:.2f}秒；峰值工作集{peak / 1024**2:.1f}MiB。",
        "全部492条记录、41题锚点及三重复稳定性通过；本次成功进程未发生内存不足或推理超时。"
        "首次沙箱网络被WinError 10013拦截；获批网络后真实抓取，未使用缓存替代。",
        "",
    ]
    if ce:
        from scripts.compare_embeddings import distribution

        t = distribution(ce["timing"]["reranking_ms"])
        result["cross_encoder"]["reranking_ms_distribution"] = t
        lines += [
            "## 唯一一次额外Cross-Encoder对照",
            "",
            f"{ce['diagnostics']['strategies']['e5_hybrid']['context_complete_case_count']}/41 → "
            f"{ce['diagnostics']['strategies']['e5_hybrid_bge']['context_complete_case_count']}/41；"
            f"额外重排P50/P95 {t['p50']:.2f}/{t['p95']:.2f}ms。",
            f"补充进程总耗时{process_timing['cross-encoder']['elapsed_seconds']:.2f}秒（含独立冷建库与暖机）。",
            "种子Top-5：029/034改善，009退化；最终上下文：029/034改善，无退化。",
            "相对旧34份语料的BGE结果38→37，仅support-006退化；本次重排没有恢复该扩容损失。"
            "support-009的种子Top-5退化由邻块恢复。",
            "",
        ]
    lines += [
        "```powershell",
        ".\\.venv\\Scripts\\python.exe -X utf8 -m scripts.compare_embeddings --documents evaluation/support_corpus_large "
        "--questions work/human-review/20261028/reviewed-retrieval-20261028T044628Z/questions.reviewed.jsonl "
        "--require-reviewed --output evaluation/reports/corpus-large-v013-REPRO.json "
        "--run-dir work/experiments/corpus-large-v013-REPRO --repeats 3",
        "```",
        "",
        "派生对比与保护校验：`python -m scripts.report_large_corpus --current <新JSON> --output <新analysis.json>`；"
        "默认基线为本机已审核47题快照，保护基准为本次启动前339份文件清单。",
        "抓取曾出现TLS连接中断；失败批次和未展开新式include的中间批次完整留在audit目录，均未用于评测。",
        "所有输出和运行目录必须尚不存在；新机器按 docs/EMBEDDING_V011.md 准备固定模型。",
        "精确重跑使用本次冻结的102份成功文档及manifest。重新执行fetch_large_corpus --transport curl "
        "可能因网络可用性得到不同成功集合；须另存目录并核对corpus_sha256，不能混称同一语料复现。",
        "",
        "## 不变性与适用边界",
        "",
        f"启动前记录的{len(before['files'])}份历史文件SHA256全部一致（旧语料、报告、trials、人工审核及既有实验）；"
        f"原34份同名文件SHA256全部一致；{len(new)}题的{sum(len(c.evidence) for c in cases)}条锚点通过。",
        f"保护快照：`{args.audit / 'protected-before.json'}`；逐文件哈希及校验结果见本报告JSON。",
        "新增版本未增加独立问题覆盖：本批问题仍仅覆盖原两个版本。30个主题的版本快照不是120个独立来源。"
        "中文翻译可能滞后，既有F001源文档事实冲突仍保留；逐字锚点通过不等于外部事实正确。",
        "未生成答案、未运行保留测试、未验证GPU/生产容量；三次重复是41题的重复测量。",
        "",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    with args.output.with_suffix(".md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    print(
        json.dumps(
            {
                "dataset": current["dataset"],
                "validation": result["validation"],
                "comparisons": {
                    k: {
                        a: v[a]
                        for a in ("baseline_complete", "large_complete", "improved", "regressed")
                    }
                    for k, v in pairs.items()
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
