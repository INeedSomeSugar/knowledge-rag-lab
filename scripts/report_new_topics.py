"""Execution and independent report writer for the admitted new-topic protocol."""

import csv
from dataclasses import asdict
import math
import platform
from statistics import mean
from time import perf_counter

from app.budget_evaluation import quantile
from app.budget_reranking import BudgetAwareReranker, GatePolicy, retrieval_signals
from app.context import context_candidates, expand_context
from app.diagnostics import span_coverage
from app.domain import SearchHit
from app.evaluation import evidence_covered, load_evaluation_cases
from app.retrieval import matches_filters
from scripts.experiment_budget_reranking import (
    compact,
    read_json,
    timestamp,
    write_json,
    write_jsonl,
)
from scripts.validate_new_topics import IDS, rows, verify_protocol

STRATEGIES = ("none", "all", "adaptive")


def coverage(case, hits):
    """运行期覆盖判定与区间明细；报告阶段会独立重算，二者不应混用。"""
    # Runtime detailed interval diagnostics. Report recomputes via evidence_covered separately.
    return {
        "complete": all(
            span_coverage(a, [h.chunk for h in hits])["complete"] for a in case.evidence
        ),
        "anchors": [span_coverage(a, [h.chunk for h in hits]) for a in case.evidence],
    }


def assemble(pool, chunks, filters, config):
    """按冻结协议拼接上下文：Top-K 截断、版本过滤、固定预算与策略。"""
    eligible = [c for c in chunks if matches_filters(c, filters)]
    return expand_context(
        pool[: config["top_k"]],
        eligible,
        char_budget=config["context_char_budget"],
        policy=config["context_policy"],
    )


def run_models(out, attempt, protocol, chunks):
    """执行一次完整的三策略对照（none / all / adaptive）。

    模型导入刻意放在函数内部：准入检查与报告生成不应因为装了 torch 就隐式加载权重。
    三策略共享同一候选池，差异只来自是否重排与门控决策。
    """
    # Deliberately lazy: preparing/rejecting admission cannot load transformers/torch or call APIs.
    import psutil
    from app.config import Settings
    from app.cross_encoder import BGEReranker, BGE_DIRECTORY
    from app.factory import create_cached_embedding
    from app.local_embeddings import E5_DIRECTORY, E5_MODEL
    from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever

    wall_start = perf_counter()
    config = protocol["configuration"]
    settings = Settings(
        embedding_provider="local_e5",
        embedding_model=E5_MODEL,
        embedding_model_dir=E5_DIRECTORY,
        embedding_device="cpu",
        embedding_cpu_threads=config["cpu_threads"],
        embedding_batch_size=config["batch_size"],
        embedding_max_length=config["max_length"],
        data_dir=attempt / "cache",
        llm_provider="extractive",
        reranker_provider="none",
    )
    environment = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "logical_cpus": psutil.cpu_count(),
        "physical_cpus": psutil.cpu_count(logical=False),
        "ram_total_bytes": psutil.virtual_memory().total,
        "available_ram_before": psutil.virtual_memory().available,
        "background_cpu_percent_before": psutil.cpu_percent(interval=0.2),
        "device": "cpu",
        "dtype": "float32",
        "cpu_threads": config["cpu_threads"],
    }
    write_json(attempt / "environment.json", environment)
    print("Loading hash-checked local E5, CPU only", flush=True)
    start = perf_counter()
    cached = create_cached_embedding(settings)
    startup = {"e5_load_verify_ms": (perf_counter() - start) * 1000}
    hybrid = HybridRetriever(
        DenseRetriever(cached),
        BM25Retriever(config["bm25_k1"], config["bm25_b"]),
        rrf_k=config["rrf_k"],
        dense_weight=config["dense_weight"],
        sparse_weight=config["sparse_weight"],
    )
    start = perf_counter()
    hybrid.index(chunks)
    startup["cold_build_ms"] = (perf_counter() - start) * 1000
    startup["document_cache"] = dict(cached.last_stats)
    print("Loading hash-checked local BGE, CPU only", flush=True)
    start = perf_counter()
    bge = BGEReranker(
        BGE_DIRECTORY,
        device="cpu",
        cpu_threads=config["cpu_threads"],
        batch_size=config["batch_size"],
        max_length=config["max_length"],
    )
    startup["bge_load_verify_ms"] = (perf_counter() - start) * 1000
    adaptive = BudgetAwareReranker(bge, GatePolicy(**config["policy"]))
    startup["warmups"] = []
    filters = {"product": "fastapi", "version": config["warmup_version"]}
    for name in STRATEGIES:
        start = perf_counter()
        pool = hybrid.search(config["warmup_question"], config["candidate_k"], filters=filters)
        calls = 0
        if name == "all":
            pool, _ = bge.rerank(config["warmup_question"], pool)
            calls = 1
        elif name == "adaptive":
            pool, trace = adaptive.rerank(config["warmup_question"], pool)
            calls = int(trace["triggered"])
        assemble(pool, chunks, filters, config)
        startup["warmups"].append(
            {
                "strategy": name,
                "ms": (perf_counter() - start) * 1000,
                "bge_calls": calls,
                "scored": False,
            }
        )
    write_json(
        attempt / "startup.json",
        {
            "at": timestamp(),
            **startup,
            "embedding": cached.model.descriptor,
            "reranker": bge.descriptor,
        },
    )
    cases = load_evaluation_cases(out / "inputs/questions.reviewed.jsonl")
    trials = []
    # Flush every completed trial; a later exception never erases partial evidence.
    with (attempt / "trials.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        import json

        for index, case in enumerate(cases):
            offset = index % 3
            order = STRATEGIES[offset:] + STRATEGIES[:offset]
            for name in order:
                start = perf_counter()
                before = hybrid.search(case.question, config["candidate_k"], filters=case.filters)
                retrieval_ms = (perf_counter() - start) * 1000
                pool, model_trace, gate_ms = before, None, 0.0
                triggered = name == "all"
                if name == "all":
                    pool, model_trace = bge.rerank(case.question, before)
                elif name == "adaptive":
                    pool, trace = adaptive.rerank(case.question, before)
                    triggered, gate_ms, model_trace = (
                        trace["triggered"],
                        trace["gate_ms"],
                        trace["model"],
                    )
                context_start = perf_counter()
                context = assemble(pool, chunks, case.filters, config)
                context_ms = (perf_counter() - context_start) * 1000
                total_ms = (perf_counter() - start) * 1000
                trial = {
                    "id": case.case_id,
                    "strategy": name,
                    "order": list(order),
                    "repeat": 0,
                    "triggered": triggered,
                    "scored_pairs": len(before) if triggered else 0,
                    "total_ms": total_ms,
                    "retrieval_ms": retrieval_ms,
                    "context_ms": context_ms,
                    "gate_ms": gate_ms,
                    "rerank_ms": model_trace["elapsed_ms"] if model_trace else 0.0,
                    "truncated_pairs": model_trace["truncated_count"] if model_trace else 0,
                    "signals": asdict(retrieval_signals(before)),
                    "before": compact(before),
                    "after": compact(pool),
                    "context_ids": [h.chunk.id for h in context],
                    "top": coverage(case, pool[: config["top_k"]]),
                    "context": coverage(case, context),
                }
                stream.write(json.dumps(trial, ensure_ascii=False) + "\n")
                stream.flush()
                trials.append(trial)
            print(f"{case.case_id}: 3/3 trials saved", flush=True)
    write_json(
        attempt / "runtime.json",
        {
            "at": timestamp(),
            "trials": len(trials),
            "process_wall_seconds": perf_counter() - wall_start,
            "paid_api_calls": 0,
            "generated_answers": 0,
            "original_held_out_8_executions": 0,
            "automatic_retries": 0,
            "warmup_trials_excluded": 3,
        },
    )


def hits_from(refs, chunk_map):
    return [
        SearchHit(
            chunk_map[r["chunk_id"]],
            r["score"],
            r.get("dense_rank"),
            r.get("sparse_rank"),
            r.get("rerank_score"),
            r.get("rerank_rank"),
        )
        for r in refs
    ]


def complete(case, hits):
    return all(evidence_covered(a, hits) for a in case.evidence)


def classify(case, pool, top, context, chunks, config):
    eligible = [c for c in chunks if matches_filters(c, case.filters)]
    if not complete(case, [SearchHit(c, 0) for c in eligible]):
        return "index_or_version_gap"
    if not complete(case, pool):
        return "recall_missing"
    if not complete(case, context):
        if complete(case, top):
            return "context_assembly"
        expanded = context_candidates(top, eligible)
        return "context_assembly" if complete(case, expanded) else "ranking_failure"
    return "covered"


def verified_cases(out):
    protocol, chunks = verify_protocol(out)
    config = protocol["configuration"]
    chunk_map = {c.id: c for c in chunks}
    cases = load_evaluation_cases(out / "inputs/questions.reviewed.jsonl")
    trials = rows(out / "attempt-001/trials.jsonl")
    if len(trials) != 36 or {(t["id"], t["strategy"]) for t in trials} != {
        (i, s) for i in IDS for s in STRATEGIES
    }:
        raise ValueError("expected exactly 12 x 3 trials, with no exclusions or duplicates")
    schedule = []
    for index, case_id in enumerate(IDS):
        order = STRATEGIES[index % 3 :] + STRATEGIES[: index % 3]
        schedule.extend((case_id, s, list(order)) for s in order)
    if [(t["id"], t["strategy"], t["order"]) for t in trials] != schedule or any(
        t["repeat"] != 0 for t in trials
    ):
        raise ValueError("trials differ from preregistered single-pass rotating schedule")
    case_map = {c.case_id: c for c in cases}
    by_key = {}
    recomputed = 0
    for t in trials:
        case = case_map[t["id"]]
        pool = hits_from(t["after"], chunk_map)
        before = hits_from(t["before"], chunk_map)
        top = pool[: config["top_k"]]
        context = [SearchHit(chunk_map[i], 0) for i in t["context_ids"]]
        if set(h.chunk.id for h in pool) != set(h.chunk.id for h in before):
            raise ValueError("reranking changed candidate membership")
        if any(
            not math.isfinite(t[k]) or t[k] < 0
            for k in ("total_ms", "retrieval_ms", "context_ms", "gate_ms", "rerank_ms")
        ):
            raise ValueError("non-finite or negative timing")
        if t["triggered"] and any(
            h.rerank_score is None or h.rerank_rank != i for i, h in enumerate(pool, 1)
        ):
            raise ValueError("actual BGE rank/score trace required for triggered trials")
        if sum(len(h.chunk.text) for h in context) > config["context_char_budget"]:
            raise ValueError("context budget exceeded")
        if len({h.chunk.id for h in pool}) != len(pool) or any(
            not matches_filters(h.chunk, case.filters) for h in before + pool + context
        ):
            raise ValueError("duplicate pool or cross-version contamination")
        if [h.chunk.id for h in assemble(pool, chunks, case.filters, config)] != t["context_ids"]:
            raise ValueError("context reconstruction differs")
        for key, hits in (("top", top), ("context", context)):
            if complete(case, hits) != t[key]["complete"]:
                raise ValueError("independently recomputed evidence coverage differs")
            recomputed += 1
        decision = GatePolicy(**config["policy"]).decide(before)
        if decision["signals"] != t["signals"] or (
            t["strategy"] == "adaptive" and decision["triggered"] != t["triggered"]
        ):
            raise ValueError("gate decision differs from frozen policy")
        if t["strategy"] != "adaptive" and t["triggered"] != (t["strategy"] == "all"):
            raise ValueError("baseline call count inconsistent")
        if t["scored_pairs"] != (len(before) if t["triggered"] else 0):
            raise ValueError("pair count differs")
        t["failure_reason"] = classify(case, pool, top, context, chunks, config)
        t["first_complete_rank"] = next(
            (k for k in range(1, len(pool) + 1) if complete(case, pool[:k])), None
        )
        t["context_chars"] = sum(len(h.chunk.text) for h in context)
        t["missing_anchors"] = [a for a in case.evidence if not evidence_covered(a, context)]
        by_key[t["id"], t["strategy"]] = t
    for case in cases:
        group = [by_key[case.case_id, s] for s in STRATEGIES]
        if any(t["before"] != group[0]["before"] for t in group[1:]):
            raise ValueError("independent retrieval executions differ between strategies")
        adaptive, all_row, none_row = group[2], group[1], group[0]
        expected = all_row if adaptive["triggered"] else none_row
        if adaptive["after"] != expected["after"]:
            raise ValueError("adaptive branch ranking differs from matching control")
    return cases, by_key, recomputed


def report(out):
    complete_run = (out / "attempt-001/outcome.json").exists()
    attempted = (out / "attempt-001").exists()
    status = (
        "completed" if complete_run else "execution_incomplete" if attempted else "awaiting_human"
    )
    dest = out / (
        "analysis-complete"
        if complete_run
        else "analysis-failed"
        if attempted
        else "analysis-pending"
    )
    if dest.exists():
        raise ValueError("report already exists; preserve earlier reports")
    audit = read_json(out / "audit.json")
    lines = ["# 新主题固定门控验证 · 2026-11-09", ""]
    case_rows, summary, differences = [], {}, []
    if not complete_run:
        lines += [
            "当前状态：等待实际人工确认。助手复核12/12；人工确认以human-review.jsonl为准。尚未执行检索评分。",
            "",
            "102文档、4版本、2212块，无语料扩充。12题证据均在原语料；4题的证据或措辞已修订，原文件保留。",
            "",
            "覆盖、重排调用量和延迟均为null（未测）。答案正确率未评测。输入校验通过不等于人工审核完成。",
            "",
            "准入后固定比较none/all/adaptive，overlap<2，单遍12×3=36次；不根据结果调参。",
            "",
            "失败原因：尚无效果运行，不归因为算法失败。当前阻塞仅为人工审核。",
            "",
            "先阅读../REVIEW.md；通过后freeze→execute→report。完整命令见../REPRODUCE.md。",
        ]
        partial_trials = (
            rows(out / "attempt-001/trials.jsonl")
            if (out / "attempt-001/trials.jsonl").exists()
            else []
        )
        if attempted:
            lines[2] = "效果执行未完整完成，所有已完成条目和失败记录原样保留。未生成三组聚合成绩。"
            lines = [s for s in lines if "当前阻塞仅为人工审核" not in s]
            lines += [
                "",
                f"已落盘完整执行条目：{len(partial_trials)}；不能按失败或指标选择排除条目。见attempt-001/failure.json及trials.jsonl。",
            ]
        for s in STRATEGIES:
            summary[s] = dict.fromkeys(
                ("n_scored", "top_complete", "context_complete", "bge_calls", "p50_ms", "p95_ms")
            )
        for q in rows(out / "inputs/questions.candidate.jsonl"):
            for s in STRATEGIES:
                case_rows.append(
                    {
                        "id": q["id"],
                        "strategy": s,
                        "status": status,
                        **dict.fromkeys(
                            (
                                "top_complete",
                                "context_complete",
                                "bge_called",
                                "total_ms",
                                "failure_reason",
                                "first_complete_rank",
                                "overlap_count",
                                "scored_pairs",
                                "context_chars",
                                "gate_errors",
                            )
                        ),
                    }
                )
        result = {
            "status": status,
            "summary": summary,
            "completed_trials": len(partial_trials),
            "attempt_started": attempted,
            "answer_accuracy": None,
            "audit": audit,
        }
    else:
        if read_json(out / "attempt-001/outcome.json").get("status") != "completed":
            raise ValueError("successful completion record required")
        cases, by_key, recomputed = verified_cases(out)
        for s in STRATEGIES:
            group = [by_key[c.case_id, s] for c in cases]
            times = [t["total_ms"] for t in group]
            summary[s] = {
                "n_scored": 12,
                "top_complete": sum(t["top"]["complete"] for t in group),
                "context_complete": sum(t["context"]["complete"] for t in group),
                "bge_calls": sum(t["triggered"] for t in group),
                "scored_pairs": sum(t["scored_pairs"] for t in group),
                "p50_ms": quantile(times, 0.5),
                "p95_ms": quantile(times, 0.95),
                "mean_ms": mean(times),
            }
        lines += [
            "人工准入后的一次固定三组CPU执行。指标是人工批准证据范围的完整覆盖，答案正确率未测。",
            "",
            "|策略|Top5完整覆盖|最终完整覆盖|BGE调用|P50 ms|P95 ms|",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for s, v in summary.items():
            lines.append(
                f"|{s}|{v['top_complete']}/12|{v['context_complete']}/12|{v['bge_calls']}/12|{v['p50_ms']:.2f}|{v['p95_ms']:.2f}|"
            )
        lines += [
            "",
            "## 逐题差异与原因",
            "",
            "|题号|overlap|按需触发|none/all/adaptive最终覆盖|三组失败原因|",
            "|---|---:|---|---|---|",
        ]
        for case in cases:
            group = {s: by_key[case.case_id, s] for s in STRATEGIES}
            adaptive = group["adaptive"]
            gate_errors = []
            if (
                not adaptive["triggered"]
                and group["all"]["context"]["complete"]
                and not adaptive["context"]["complete"]
            ):
                gate_errors.append("gate_false_skip")
            if (
                adaptive["triggered"]
                and group["none"]["context"]["complete"]
                and not adaptive["context"]["complete"]
            ):
                gate_errors.append("gate_harmful_call")
            if (
                adaptive["triggered"]
                and not gate_errors
                and group["none"]["context"]["complete"] == adaptive["context"]["complete"]
            ):
                gate_errors.append("gate_unhelpful_call")
            differences.append(
                {
                    "id": case.case_id,
                    "question": case.question,
                    "reference_answer": case.reference_answer,
                    "filters": case.filters,
                    "evidence": list(case.evidence),
                    "gate_errors": gate_errors,
                    "strategies": group,
                }
            )
            flags = "/".join(str(int(group[s]["context"]["complete"])) for s in STRATEGIES)
            reasons = "/".join(group[s]["failure_reason"] for s in STRATEGIES)
            lines.append(
                f"|{case.case_id[-3:]}|{adaptive['signals']['overlap_count']}|{adaptive['triggered']}|{flags}|{reasons}; {','.join(gate_errors)}|"
            )
            for s, t in group.items():
                case_rows.append(
                    {
                        "id": case.case_id,
                        "strategy": s,
                        "status": "completed",
                        "top_complete": t["top"]["complete"],
                        "context_complete": t["context"]["complete"],
                        "bge_called": t["triggered"],
                        "total_ms": t["total_ms"],
                        "failure_reason": t["failure_reason"],
                        "first_complete_rank": t["first_complete_rank"],
                        "overlap_count": t["signals"]["overlap_count"],
                        "scored_pairs": t["scored_pairs"],
                        "context_chars": t["context_chars"],
                        "gate_errors": ",".join(gate_errors) if s == "adaptive" else "",
                    }
                )
        saved = 1 - summary["adaptive"]["bge_calls"] / summary["all"]["bge_calls"]
        failures = [d["strategies"]["adaptive"]["failure_reason"] for d in differences]
        if any("gate_false_skip" in d["gate_errors"] for d in differences):
            next_step = "下一轮只比较门控信号：在相同触发预算下比较overlap与RRF分差单信号，阈值仅在开发数据确定；其余召回、BGE和装配不变。"
        elif "recall_missing" in failures:
            next_step = "下一轮只把融合后候选池50改为100，保持Top5、模型、门控与装配不变，检验池外证据能否回来，同时记录翻倍打分对数的成本。"
        elif "ranking_failure" in failures:
            next_step = "下一轮只给BGE候选正文加文档标题/章节前缀，候选集合和其他参数不变，检验池内误排；同时记录512-token截断是否恶化。"
        elif "context_assembly" in failures:
            next_step = "下一轮只比较neighbors与已有merged_neighbors，保持8000字符预算及全部种子排名不变，检查装配收益和挤出退化。"
        else:
            next_step = "本批未定位到上述覆盖缺口；下一轮只把BGE待打分候选50缩至20，保持其余链路不变，检验覆盖与CPU延迟取舍。"
        next_step += " 本12题此后作为回归材料；下一轮须另审新题并预先冻结验证方案。"
        lines += [
            "",
            f"按需相对全量减少BGE调用{saved:.1%}；调用节约不等于延迟同比下降。独立重算{recomputed}项逐次覆盖检查通过。",
            "",
            "召回缺失指融合后50候选不完整，不等同于两路原始召回均缺失。排序失败指池内证据未被Top5及同节邻块恢复。装配失败指可扩展证据受预算装配丢失。门控误跳过单列。",
            "",
            "6主题/12道助手拟题、同一FastAPI文档域，只支持这批小规模验证；不能推断跨域或真实用户分布。36次不是36道独立题。",
            "",
            "计时为每组12个样本的单遍暖态串行观测，perf_counter含查询编码、检索、门控、真实BGE和上下文；不含评分写盘。加载/冷建库/3次无金标暖机另列startup.json。没有并发或SLA结论。",
            "",
            "不重调阈值。不与开发41题合并，不运行原8道测试，不生成答案，不调用付费API。",
            "",
            next_step,
        ]
        result = {
            "status": "completed",
            "summary": summary,
            "retrieval_executions": 36,
            "answer_accuracy": None,
            "independent_coverage_checks": recomputed,
            "audit": audit,
            "differences": differences,
            "next_controlled_experiment": next_step,
        }
    dest.mkdir()
    write_json(dest / "results.json", result)
    write_jsonl(dest / "per-case.jsonl", case_rows)
    with (dest / "per-case.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(case_rows[0]))
        writer.writeheader()
        writer.writerows(case_rows)
    (dest / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    raise SystemExit("Use: python -m scripts.validate_new_topics report")
