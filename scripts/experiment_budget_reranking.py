"""Prepare a frozen dev gate, then execute real CPU retrieval/reranking (no API).

两阶段协议，目的是把“选阈值”和“测效果”彻底分开：

- ``prepare``：只在已人工核验的开发集上重放冻结的检索结果，选出唯一门控策略并写
  ``selection.json`` + 哈希；同时冻结语料、问题、配置与源码。
- ``execute``：校验冻结文件未变，再用真实本地 E5/BGE 在 CPU 上跑
  ``41 题 × 3 策略 × 3 轮``，逐条写盘。

关键约束（全部硬编码校验，不是建议）：

- 只允许 development + CPU + 固定 ``candidate_k=50`` / ``rrf_k=60``；
- 输出目录必须是新目录且位于项目内，绝不覆盖既有实验；
- 运行前后校验受保护文件哈希，确保没有“边跑边改”；
- 不计费的探针（warmup）单独记录，不进入分位数统计。
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
from statistics import mean
import subprocess
from time import perf_counter
import zipfile

from app.budget_evaluation import (
    budget_frontier,
    grouped_validation,
    quantile,
    score_evidence,
    select_policy,
)
from app.budget_reranking import BudgetAwareReranker, GatePolicy, retrieval_signals
from app.chunking import TextChunker
from app.config import Settings
from app.context import expand_context
from app.cross_encoder import BGEReranker, BGE_DIRECTORY
from app.domain import SearchHit
from app.evaluation import load_evaluation_cases
from app.factory import create_cached_embedding
from app.local_embeddings import E5_DIRECTORY, E5_MODEL, sha256_file
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever, matches_filters
from scripts.evaluate import load_corpus, validate_evidence, validate_relevant_sources

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ("none", "all", "adaptive")
# 进入冻结清单的源码：任何影响检索/门控/评测结果的文件都必须在此列，
# 否则“同一份 selection 换个实现重跑”就无法被发现。
SOURCES = [
    "app/budget_reranking.py",
    "app/budget_evaluation.py",
    "app/retrieval.py",
    "app/cross_encoder.py",
    "app/local_embeddings.py",
    "app/context.py",
    "app/evaluation.py",
    "app/chunking.py",
    "app/corpus.py",
    "app/factory.py",
    "app/vector_cache.py",
    "app/domain.py",
    "scripts/evaluate.py",
    "scripts/experiment_budget_reranking.py",
]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    """独占创建（``x``）：同名结果已存在时直接失败，保留原始实验。"""
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_jsonl(path, rows):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def get_inputs(config):
    """加载并校验输入：只接受人工核验过的开发题。

    这一步在加载模型之前完成，避免“跑了几分钟才发现数据不合格”。
    """
    # Reject mixed files before corpus/model loading. Test rows are never selected or scored.
    cases = load_evaluation_cases(Path(config["questions"]))
    if not cases or any(
        c.split != "development" or c.review_status != "human_verified" for c in cases
    ):
        raise ValueError("input must contain ONLY human-reviewed development cases")
    documents = Path(config["documents"])
    validate_evidence(cases, documents)
    chunks, sources = load_corpus(
        documents, TextChunker(config["chunk_size"], config["chunk_overlap"], strategy="sections")
    )
    validate_relevant_sources(cases, sources)
    return cases, chunks, sources


def protections(run_dir, output_dir):
    """记录本次实验不应改动的文件哈希快照（排除本次输出目录自身）。"""
    paths = set()
    for root in [
        Path("evaluation"),
        Path("work/human-review"),
        Path("work/experiments"),
        Path(".github"),
        Path("scripts/finetune_retriever"),
    ]:
        if root.exists():
            paths.update(
                p
                for p in root.rglob("*")
                if p.is_file()
                and not p.resolve().is_relative_to(run_dir.resolve())
                and not p.resolve().is_relative_to(output_dir.resolve())
                and "__pycache__" not in p.parts
            )
    return {p.as_posix(): sha256_file(p) for p in sorted(paths)}


def verify_hashes(mapping):
    """任何缺失或变化都视为实验失效；不尝试修复或忽略。"""
    changed = [
        p
        for p, digest in mapping.items()
        if not Path(p).is_file() or sha256_file(Path(p)) != digest
    ]
    if changed:
        raise ValueError(f"protected/locked input changed: {changed}")
    return {"checked": len(mapping), "changed": []}


def compact(hits):
    """把 SearchHit 压成可比较的定位+排名记录，正文不入 trials。"""
    return [
        {
            "chunk_id": h.chunk.id,
            "score": h.score,
            "dense_rank": h.dense_rank,
            "sparse_rank": h.sparse_rank,
            "rerank_score": h.rerank_score,
            "rerank_rank": h.rerank_rank,
        }
        for h in hits
    ]


def assemble(pool, chunks, case, config):
    """在版本过滤后的候选上拼接上下文，与服务默认策略保持一致。"""
    eligible = [c for c in chunks if matches_filters(c, case.filters)]
    return expand_context(
        pool[: config["top_k"]],
        eligible,
        char_budget=config["context_char_budget"],
        policy=config["context_policy"],
    )


def prepare(args, config):
    """重放冻结诊断结果，选出唯一门控并写出带哈希的冻结件。

    这里刻意“不重新检索”：候选池与两路排名直接取自既有诊断报告，只重算上下文与
    证据覆盖。这样阈值选择只依赖已冻结数据，换机器重跑也能得到同一策略。

    最后写出：

    - ``selection.json`` + ``selection.sha256``：策略与全部输入哈希；
    - ``replay-cases.jsonl``：逐题信号与两种端点的覆盖结果；
    - ``group-validation.json``：按组交叉验证明细；
    - ``source.zip`` / ``initial-state.json``：实现与启动状态快照。
    """
    if args.output_dir.exists() or args.run_dir.exists():
        raise ValueError("prepare needs NEW output-dir and run-dir; never overwrite experiments")
    args.output_dir.mkdir(parents=True)
    args.run_dir.mkdir(parents=True)
    protected = protections(args.run_dir, args.output_dir)
    write_json(args.run_dir / "protected-before.json", protected)
    cases, chunks, sources = get_inputs(config)
    report = read_json(config["replay_report"])
    trials_path = Path(config["replay_trials"])
    if sha256_file(trials_path) != report["trials_sha256"]:
        raise ValueError("frozen replay trials hash mismatch")
    expected = {s: sha256_file(Path(config["documents"]) / s) for s in sorted(sources)}
    if expected != report["dataset"]["corpus_files"]:
        raise ValueError("replay corpus mismatch")
    chunk_map = {c.id: c for c in chunks}
    old_cases = {r["id"]: r for r in report["diagnostics"]["cases"]}
    if set(old_cases) != {c.case_id for c in cases}:
        raise ValueError("development case identity mismatch")
    for case in cases:
        old = old_cases[case.case_id]
        # 逐字段比对问题语义：题面、版本范围、证据、划分、期望状态任一变化都不能重放。
        for field, current in [
            ("question", case.question),
            ("filters", case.filters),
            ("evidence", list(case.evidence)),
            ("split", case.split),
            ("expected_status", case.expected_status),
        ]:
            if old[field] != current:
                raise ValueError(f"replay question semantics changed: {case.case_id} {field}")
    trials = [json.loads(line) for line in trials_path.read_text(encoding="utf-8").splitlines()]
    if len(trials) != len({t["id"] for t in trials}):
        raise ValueError("expected one frozen trial per answerable case")
    trial_map = {t["id"]: t for t in trials}
    rows = []
    for case in cases:
        if not case.should_answer:
            continue
        trial, old = trial_map[case.case_id], old_cases[case.case_id]
        pools = {}
        for name, key, old_name in [
            ("none", "before", "e5_hybrid"),
            ("all", "after", "e5_hybrid_bge"),
        ]:
            # Frozen trials round RRF scores to six decimals: use full-precision diagnostic scores.
            refs = {h["chunk_id"]: h for h in old["strategies"][old_name]["pool"]}
            hits = []
            for h in trial[key]:
                c = chunk_map[h["chunk_id"]]
                # 分块内容必须逐字一致，否则“同一候选”只是 ID 相同。
                if any(h[k] != getattr(c, k) for k in ["source", "text", "start_char", "end_char"]):
                    raise ValueError("replay chunk content mismatch")
                ref = refs[c.id]
                hits.append(
                    SearchHit(
                        c,
                        ref["score"],
                        ref["dense_rank"],
                        ref["sparse_rank"],
                        h.get("rerank_score"),
                        h.get("rerank_rank"),
                    )
                )
            context = assemble(hits, chunks, case, config)
            pools[name] = {
                **score_evidence(case, hits[: config["top_k"]], context),
                "pool_ids": [h.chunk.id for h in hits],
                "context_ids": [h.chunk.id for h in context],
            }
            prior = old["strategies"][old_name]
            # 重算结果必须与冻结报告一致；不一致说明代码或语料已变，不能继续选阈值。
            if pools[name]["context_complete"] != (prior["context_evidence_recall"] == 1) or pools[
                name
            ]["top_complete"] != (prior["top_k_evidence_recall"] == 1):
                raise ValueError("recomputed frozen evidence differs")
            if name == "none":
                # 门控信号只从“重排前”候选计算，绝不能带 BGE 分数。
                signals = asdict(retrieval_signals(hits))
        rows.append({"id": case.case_id, "group_id": case.group_id, "signals": signals, **pools})
    policies = [
        GatePolicy(family, threshold)
        for family in ("overlap", "margin")
        for threshold in config[family + "_thresholds"]
    ]
    selected, results = select_policy(rows, policies)
    folds = grouped_validation(rows, policies, config["group_folds"])
    # 冻结清单覆盖配置、问题、重放结果、源码与语料 manifest。
    locked_paths = [
        args.config,
        Path(config["plan"]),
        Path(config["questions"]),
        Path(config["replay_report"]),
        Path(config["replay_trials"]),
        *[Path(p) for p in SOURCES],
        Path(config["documents"]) / "manifest.json",
        *[Path(config["documents"]) / s for s in sorted(sources)],
    ]
    lock = {p.as_posix(): sha256_file(p) for p in locked_paths}
    selection = {
        "created_at": timestamp(),
        "phase": "development_replay_selection_frozen",
        "selected": selected,
        "target_trigger_rate": config["target_trigger_rate"],
        "target_met": selected["trigger_rate"] <= config["target_trigger_rate"],
        "lock": lock,
        "configuration": config,
    }
    write_json(args.output_dir / "selection.json", selection)
    with (args.output_dir / "selection.sha256").open("x", encoding="utf-8") as f:
        f.write(sha256_file(args.output_dir / "selection.json") + "\n")
    write_json(
        args.output_dir / "replay.json",
        {
            "created_at": timestamp(),
            "scope": "cached replay, NOT real inference timing",
            "n": len(rows),
            "documents": len(sources),
            "chunks": len(chunks),
            "grid": results,
            "frontier": budget_frontier(results, config["budget_rates"]),
            "skipped": [
                {"id": c.case_id, "reason": "unanswerable/clarification excluded from retrieval"}
                for c in cases
                if not c.should_answer
            ],
            "recomputed_prior_coverage_matches": True,
            "paid_api_calls": 0,
            "held_out_cases": 0,
        },
    )
    write_jsonl(args.output_dir / "replay-cases.jsonl", rows)
    write_json(args.output_dir / "group-validation.json", folds)
    (args.run_dir / "questions.snapshot.jsonl").write_bytes(Path(config["questions"]).read_bytes())
    with zipfile.ZipFile(args.run_dir / "source.zip", "x", compression=zipfile.ZIP_DEFLATED) as z:
        # 只打包源码与配置，不把语料和权重塞进 zip（它们在 lock 里另有哈希）。
        for p in locked_paths:
            if p.suffix in {".py", ".md"} or p == args.config:
                z.write(p, p.as_posix())
    write_json(
        args.run_dir / "initial-state.json",
        {
            "created_at": timestamp(),
            "git_status": subprocess.check_output(
                ["git", "status", "--short"], text=True, encoding="utf-8"
            ),
            "questions_sha256": sha256_file(Path(config["questions"])),
            "selected_policy_sha256": sha256_file(args.output_dir / "selection.json"),
            "protected_files": len(protected),
        },
    )
    print(
        json.dumps(
            {
                "selected": selected,
                "group_validation": {
                    k: folds[k] for k in ["n", "context_complete", "top_complete", "trigger_count"]
                },
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def timing_summary(trials):
    """按策略汇总耗时分布。

    同时给出 overall 与 by_repeat：后者用于检查首轮是否因缓存冷启动而系统性偏慢。
    所有分位数都用 ``quantile`` 的线性插值口径，与报告图表一致。
    """
    result = {}
    for name in STRATEGIES:
        subset = [t for t in trials if t["strategy"] == name]
        timings = {}
        for field in ("total_ms", "retrieval_ms", "gate_ms", "rerank_ms", "context_ms"):
            values = [t[field] for t in subset]
            timings[field] = {
                "p50": quantile(values, 0.5),
                "p95": quantile(values, 0.95),
                "mean": mean(values),
                "sum": sum(values),
            }
        result[name] = {
            "n": len(subset),
            "timings": timings,
            "actual_calls": sum(t["triggered"] for t in subset),
            "scored_pairs": sum(t["scored_pairs"] for t in subset),
            "by_repeat": {
                str(repeat): {
                    "p50": quantile([t["total_ms"] for t in subset if t["repeat"] == repeat], 0.5),
                    "p95": quantile([t["total_ms"] for t in subset if t["repeat"] == repeat], 0.95),
                }
                for repeat in sorted({t["repeat"] for t in subset})
            },
        }
    return result


def execute(args, config):
    """真实执行：固定 CPU、固定候选池，逐条计时并落盘。

    计时边界写得很窄：只计“检索 + 门控/重排 + 上下文拼接”。金标评分、trace 格式化
    与磁盘写入都在计时区之外，避免把评测开销算进系统延迟。

    每轮按轮转顺序执行三种策略，抵消“总是先跑 none 所以更快”的顺序偏差；
    ``stable_ranking`` / ``same_ranking_as_replay`` 等布尔位用于事后核验一致性。
    """
    wall_started = perf_counter()
    selection_path = args.output_dir / "selection.json"
    selection = read_json(selection_path)
    if sha256_file(selection_path) != (args.output_dir / "selection.sha256").read_text().strip():
        raise ValueError("frozen selection has changed")
    verify_hashes(selection["lock"])
    verify_hashes(read_json(args.run_dir / "protected-before.json"))
    for p in [
        args.output_dir / "real.json",
        args.output_dir / "real.trials.jsonl",
        args.run_dir / "cache",
        args.run_dir / "runtime-start.json",
    ]:
        if p.exists():
            raise ValueError("execute needs unused runtime outputs; preserve partial runs")
    cases, chunks, sources = get_inputs(config)
    selected_cases = [c for c in cases if c.should_answer and c.evidence]
    # 显式构造 Settings 而不是读 .env：实验参数必须写死在协议里，不受本机环境影响。
    settings = Settings(
        embedding_provider="local_e5",
        embedding_model=E5_MODEL,
        embedding_model_dir=E5_DIRECTORY,
        embedding_device="cpu",
        embedding_cpu_threads=config["cpu_threads"],
        embedding_batch_size=8,
        embedding_max_length=512,
        data_dir=args.run_dir / "cache",
        llm_provider="extractive",
        reranker_provider="none",
    )
    import psutil

    process = psutil.Process()
    environment = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "logical_cpus": psutil.cpu_count(),
        "physical_cpus": psutil.cpu_count(logical=False),
        "ram_total_bytes": psutil.virtual_memory().total,
        "available_ram_before": psutil.virtual_memory().available,
        "background_cpu_percent_before": psutil.cpu_percent(interval=0.2),
        "device": "cpu",
        "cuda_used": False,
        "cpu_threads": config["cpu_threads"],
    }
    startup = {}
    started = perf_counter()
    print("Loading offline E5 (hash verification included)", flush=True)
    cached = create_cached_embedding(settings)
    startup["e5_load_verify_ms"] = (perf_counter() - started) * 1000
    started = perf_counter()
    dense, sparse = DenseRetriever(cached), BM25Retriever()
    hybrid = HybridRetriever(dense, sparse)
    hybrid.index(chunks)
    # 冷建库耗时单列：它包含首次向量编码，不应摊进单次查询延迟。
    startup["cold_build_ms"] = (perf_counter() - started) * 1000
    startup["document_cache"] = dict(cached.last_stats)
    started = perf_counter()
    print("Loading offline BGE (CPU only)", flush=True)
    bge = BGEReranker(BGE_DIRECTORY, cpu_threads=config["cpu_threads"])
    startup["bge_load_verify_ms"] = (perf_counter() - started) * 1000
    adaptive = BudgetAwareReranker(bge, GatePolicy(**selection["selected"]["policy"]))
    startup["warmup"] = []
    first = selected_cases[0]
    # 用第一题跑三种策略各一次做暖机：触发权重加载与内存分配，结果不计入统计。
    for name in STRATEGIES:
        started = perf_counter()
        pool = hybrid.search(first.question, config["candidate_k"], filters=first.filters)
        if name == "all":
            pool, _ = bge.rerank(first.question, pool)
        elif name == "adaptive":
            pool, _ = adaptive.rerank(first.question, pool)
        assemble(pool, chunks, first, config)
        startup["warmup"].append(
            {"strategy": name, "elapsed_ms": (perf_counter() - started) * 1000}
        )
    write_json(
        args.run_dir / "runtime-start.json",
        {
            "created_at": timestamp(),
            "pid": process.pid,
            "environment": environment,
            "startup": startup,
            "selection_sha256": sha256_file(selection_path),
            "embedding": cached.model.descriptor,
            "reranker": bge.descriptor,
        },
    )
    print("Warmup complete; running 41 x 3 x 3 independent query executions", flush=True)
    replay = {
        r["id"]: r
        for r in (
            json.loads(line)
            for line in (args.output_dir / "replay-cases.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
    }
    trials, reference, first_scores = [], {}, {}
    with (args.output_dir / "real.trials.jsonl").open("x", encoding="utf-8", newline="\n") as f:
        for repeat in range(config["repeats"]):
            for index, case in enumerate(selected_cases):
                # 轮转顺序：每题的三种策略起始位置不同，避免固定顺序带来的系统偏差。
                offset = (index + repeat) % len(STRATEGIES)
                order = STRATEGIES[offset:] + STRATEGIES[:offset]
                for name in order:
                    started = perf_counter()
                    pool = hybrid.search(case.question, config["candidate_k"], filters=case.filters)
                    retrieval_ms = (perf_counter() - started) * 1000
                    # before 用于事后重算门控信号；pool 会被重排覆盖。
                    before = pool
                    gate_ms, model_trace = 0.0, None
                    triggered = name == "all"
                    if name == "all":
                        pool, model_trace = bge.rerank(case.question, pool)
                    elif name == "adaptive":
                        pool, adaptive_trace = adaptive.rerank(case.question, pool)
                        gate_ms = adaptive_trace["gate_ms"]
                        triggered = adaptive_trace["triggered"]
                        model_trace = adaptive_trace["model"]
                    context_start = perf_counter()
                    context = assemble(pool, chunks, case, config)
                    context_ms = (perf_counter() - context_start) * 1000
                    total_ms = (perf_counter() - started) * 1000
                    # Gold scoring, trace formatting and disk writes are outside timed region.
                    score = score_evidence(case, pool[: config["top_k"]], context)
                    expected = replay[case.case_id]["all" if triggered else "none"]
                    ids = [h.chunk.id for h in pool]
                    previous = reference.setdefault((name, case.case_id), ids)
                    first_scores.setdefault((name, case.case_id), score)
                    row = {
                        "id": case.case_id,
                        "group_id": case.group_id,
                        "strategy": name,
                        "repeat": repeat,
                        "order": order,
                        "triggered": triggered,
                        # scored_pairs 记录真实参与打分的候选数，是“省了多少次前向”的直接证据。
                        "scored_pairs": len(before) if triggered else 0,
                        "total_ms": total_ms,
                        "retrieval_ms": retrieval_ms,
                        "gate_ms": gate_ms,
                        "context_ms": context_ms,
                        "rerank_ms": model_trace["elapsed_ms"] if model_trace else 0.0,
                        "truncated_pairs": model_trace["truncated_count"] if model_trace else 0,
                        "signals": asdict(retrieval_signals(before)),
                        "gate_matches_frozen_selection": name != "adaptive"
                        or triggered == selection["selected"]["decisions"][case.case_id],
                        "stable_ranking": previous == ids,
                        "same_ranking_as_replay": ids == expected["pool_ids"],
                        "same_coverage_as_replay": all(
                            score[k] == expected[k] for k in ("top_complete", "context_complete")
                        ),
                        "before": compact(before),
                        "after": compact(pool),
                        "context_ids": [h.chunk.id for h in context],
                        "evidence": score,
                    }
                    trials.append(row)
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    # 逐条 flush：中途崩溃也能保留已完成记录，便于续跑与审计。
                    f.flush()
                print(
                    f"repeat {repeat + 1}/{config['repeats']} {case.case_id} complete", flush=True
                )
    summary = {}
    per_case = []
    for case in selected_cases:
        per_case.append(
            {
                "id": case.case_id,
                "group_id": case.group_id,
                "question": case.question,
                "filters": case.filters,
                "gold_evidence": list(case.evidence),
                "signals": replay[case.case_id]["signals"],
                # 逐题只取每策略第一次执行的结果：三轮用于稳定性检查，不用于挑最好的一轮。
                "strategies": {n: first_scores[n, case.case_id] for n in STRATEGIES},
            }
        )
    for name in STRATEGIES:
        summary[name] = {
            "n": len(selected_cases),
            "top_complete": sum(r["strategies"][name]["top_complete"] for r in per_case),
            "context_complete": sum(r["strategies"][name]["context_complete"] for r in per_case),
            "triggered_queries": sum(
                t["triggered"] for t in trials if t["strategy"] == name and t["repeat"] == 0
            ),
        }
    # 与两个端点分别做逐题配对：gained/lost 直接列出题号，便于人工复核是否有系统性退化。
    transitions = {}
    for baseline in ("none", "all"):
        for metric in ("top_complete", "context_complete"):
            transitions[f"adaptive_vs_{baseline}_{metric}"] = {
                "gained": [
                    r["id"]
                    for r in per_case
                    if r["strategies"]["adaptive"][metric] and not r["strategies"][baseline][metric]
                ],
                "lost": [
                    r["id"]
                    for r in per_case
                    if not r["strategies"]["adaptive"][metric] and r["strategies"][baseline][metric]
                ],
            }
    # 收尾再校验一次：运行期间任何受保护文件被改动，本次结果作废。
    protection = verify_hashes(read_json(args.run_dir / "protected-before.json"))
    verify_hashes(selection["lock"])
    real = {
        "created_at": timestamp(),
        "scope": "real_local_cpu_retrieval_only_development",
        "configuration": config,
        "selected_policy": selection["selected"]["policy"],
        "selection_sha256": sha256_file(selection_path),
        "environment": environment,
        "startup": startup,
        "embedding": cached.model.descriptor,
        "reranker": bge.descriptor,
        "summary": summary,
        "timing": timing_summary(trials),
        "transitions": transitions,
        "cases": per_case,
        "documents": len(sources),
        "chunks": len(chunks),
        "execution": {
            "trials": len(trials),
            "paid_api_calls": 0,
            "generated_answers": 0,
            "held_out_cases": 0,
            "warmups_excluded": 3,
            "unstable_rankings": sum(not t["stable_ranking"] for t in trials),
            "replay_ranking_mismatches": sum(not t["same_ranking_as_replay"] for t in trials),
            "replay_coverage_mismatches": sum(not t["same_coverage_as_replay"] for t in trials),
            "gate_decision_mismatches": sum(not t["gate_matches_frozen_selection"] for t in trials),
            "process_wall_seconds": perf_counter() - wall_started,
        },
        "protected_inputs": protection,
        "trials_sha256": sha256_file(args.output_dir / "real.trials.jsonl"),
    }
    write_json(args.output_dir / "real.json", real)
    print(
        json.dumps(
            {"summary": summary, "timing": real["timing"], "execution": real["execution"]},
            ensure_ascii=False,
        ),
        flush=True,
    )


def main():
    """命令行入口：只接受 prepare / execute 两个阶段，并强制协议约束。

    必须从项目根目录执行；配置只允许 development + CPU + 固定候选池与 RRF 参数；
    输出目录不能包含或位于原始输入、模型权重、无关工作目录之内。
    """
    if Path.cwd().resolve() != ROOT:
        raise ValueError("run from the project root")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=["prepare", "execute"])
    p.add_argument(
        "--config", type=Path, default=Path("evaluation/budget_reranking/config-20261106.json")
    )
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    args = p.parse_args()
    config = read_json(args.config)
    if config["split"] != "development" or config["device"] != "cpu":
        p.error("this protocol permits only development and CPU")
    if not 1 <= config["repeats"] <= 3 or config["candidate_k"] != 50 or config["rrf_k"] != 60:
        p.error("invalid fixed protocol")
    for target in (args.output_dir.resolve(), args.run_dir.resolve()):
        if not target.is_relative_to(ROOT) or target == ROOT:
            p.error("outputs must be inside this project")
        for root in (
            Path(config["documents"]),
            E5_DIRECTORY,
            BGE_DIRECTORY,
            Path("work/human-review"),
            Path("scripts/finetune_retriever"),
            Path(".github"),
        ):
            if target.is_relative_to(root.resolve()):
                p.error("outputs must not touch original inputs or unrelated work")
    # 两个目录互为父子会导致“清理输出时误删运行记录”，直接拒绝。
    if args.output_dir.resolve().is_relative_to(
        args.run_dir.resolve()
    ) or args.run_dir.resolve().is_relative_to(args.output_dir.resolve()):
        p.error("run-dir and output-dir must not contain each other")
    (prepare if args.phase == "prepare" else execute)(args, config)


if __name__ == "__main__":
    main()
