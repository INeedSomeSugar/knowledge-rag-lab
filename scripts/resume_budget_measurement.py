"""Complete only missing scheduled trials, retaining an immutable interrupted prefix."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import platform
import shutil
from time import perf_counter

from scripts.experiment_budget_reranking import (
    STRATEGIES,
    BGEReranker,
    BGE_DIRECTORY,
    BM25Retriever,
    BudgetAwareReranker,
    DenseRetriever,
    E5_DIRECTORY,
    E5_MODEL,
    GatePolicy,
    HybridRetriever,
    Settings,
    assemble,
    compact,
    create_cached_embedding,
    get_inputs,
    read_json,
    retrieval_signals,
    score_evidence,
    sha256_file,
    timestamp,
    timing_summary,
    verify_hashes,
    write_json,
)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "source-run", "output-dir", "run-dir"):
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != root:
        p.error("run from project root")
    for target in (args.output_dir, args.run_dir):
        if target.exists() or not target.resolve().is_relative_to(root):
            p.error("new project-local directories required")
    if (args.source / "real.json").exists():
        p.error("source is already complete")
    import psutil

    old_runtime = read_json(args.source_run / "runtime-start.json")
    if psutil.pid_exists(old_runtime["pid"]):
        p.error("source process ID still exists")
    selection = read_json(args.source / "selection.json")
    selection_hash = sha256_file(args.source / "selection.json")
    assert selection_hash == (args.source / "selection.sha256").read_text().strip()
    verify_hashes(selection["lock"])
    verify_hashes(read_json(args.source_run / "protected-before.json"))
    config = selection["configuration"]
    assert config["device"] == "cpu" and config["split"] == "development"
    cases, chunks, sources = get_inputs(config)
    cases = [c for c in cases if c.should_answer and c.evidence]
    schedule = []
    for repeat in range(config["repeats"]):
        for index, case in enumerate(cases):
            offset = (index + repeat) % 3
            order = STRATEGIES[offset:] + STRATEGIES[:offset]
            schedule.extend((repeat, case, name, order) for name in order)
    raw_path = args.source / "real.trials.jsonl"
    prefix = [json.loads(s) for s in raw_path.read_text(encoding="utf-8").splitlines()]
    assert 0 < len(prefix) < len(schedule)
    for row, (repeat, case, name, order) in zip(prefix, schedule, strict=False):
        assert (row["repeat"], row["id"], row["strategy"]) == (repeat, case.case_id, name)
        assert row["order"] == list(order)
    args.output_dir.mkdir(parents=True)
    args.run_dir.mkdir(parents=True)
    for name in (
        "selection.json",
        "selection.sha256",
        "replay.json",
        "replay-cases.jsonl",
        "group-validation.json",
    ):
        shutil.copyfile(args.source / name, args.output_dir / name)
    for name in (
        "protected-before.json",
        "questions.snapshot.jsonl",
        "source.zip",
        "initial-state.json",
    ):
        shutil.copyfile(args.source_run / name, args.run_dir / name)
    shutil.copyfile(raw_path, args.run_dir / "prefix.original.jsonl")
    shutil.copyfile(
        args.source_run / "runtime-start.json", args.run_dir / "prefix.runtime-start.json"
    )
    shutil.copyfile(__file__, args.run_dir / "resume.source.py")
    recovery = {
        "created_at": timestamp(),
        "mode": "immutable_prefix_plus_missing_keys",
        "original_output": str(args.source),
        "original_runtime": str(args.source_run),
        "partial_trials": len(prefix),
        "partial_sha256": sha256_file(raw_path),
        "source_pid_absent": True,
        "selected_policy_sha256": selection_hash,
        "retuned": False,
        "cause": "process absent after tool reset; exit/cause unverified",
        "amendment_sha256": sha256_file(
            Path("evaluation/budget_reranking/recovery-amendment-20261106.md")
        ),
        "generator_sha256": sha256_file(Path(__file__)),
    }
    write_json(args.run_dir / "recovery.json", recovery)
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
    environment = dict(old_runtime["environment"])
    assert environment["platform"] == platform.platform()
    startup = {}
    print(
        f"Retaining {len(prefix)} trials; executing {len(schedule) - len(prefix)} missing keys",
        flush=True,
    )
    started = perf_counter()
    cached = create_cached_embedding(settings)
    startup["e5_load_verify_ms"] = (perf_counter() - started) * 1000
    started = perf_counter()
    hybrid = HybridRetriever(DenseRetriever(cached), BM25Retriever())
    hybrid.index(chunks)
    startup["cold_build_ms"] = (perf_counter() - started) * 1000
    startup["document_cache"] = dict(cached.last_stats)
    started = perf_counter()
    bge = BGEReranker(BGE_DIRECTORY, cpu_threads=config["cpu_threads"])
    startup["bge_load_verify_ms"] = (perf_counter() - started) * 1000
    adaptive = BudgetAwareReranker(bge, GatePolicy(**selection["selected"]["policy"]))
    startup["warmup"] = []
    for name in STRATEGIES:
        started = perf_counter()
        pool = hybrid.search(cases[0].question, config["candidate_k"], filters=cases[0].filters)
        if name != "none":
            pool, _ = (bge if name == "all" else adaptive).rerank(cases[0].question, pool)
        assemble(pool, chunks, cases[0], config)
        startup["warmup"].append(
            {"strategy": name, "elapsed_ms": (perf_counter() - started) * 1000}
        )
    runtime = {
        "created_at": timestamp(),
        "pid": psutil.Process().pid,
        "environment": environment,
        "startup": startup,
        "selection_sha256": selection_hash,
        "embedding": cached.model.descriptor,
        "reranker": bge.descriptor,
    }
    assert runtime["embedding"] == old_runtime["embedding"]
    assert runtime["reranker"] == old_runtime["reranker"]
    write_json(args.run_dir / "runtime-start.json", runtime)
    replay = {
        r["id"]: r
        for r in [
            json.loads(s)
            for s in (args.output_dir / "replay-cases.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
    }
    reference = {(r["strategy"], r["id"]): [h["chunk_id"] for h in r["after"]] for r in prefix}
    trials = [{**r, "measurement_segment": 0} for r in prefix]
    with (args.output_dir / "real.trials.jsonl").open("x", encoding="utf-8", newline="\n") as f:
        for r in trials:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.flush()
        for repeat, case, name, order in schedule[len(prefix) :]:
            started = perf_counter()
            pool = hybrid.search(case.question, config["candidate_k"], filters=case.filters)
            retrieval_ms = (perf_counter() - started) * 1000
            before, gate_ms, trace, triggered = pool, 0.0, None, name == "all"
            if name == "all":
                pool, trace = bge.rerank(case.question, pool)
            elif name == "adaptive":
                pool, decision = adaptive.rerank(case.question, pool)
                gate_ms, triggered, trace = (
                    decision["gate_ms"],
                    decision["triggered"],
                    decision["model"],
                )
            context_started = perf_counter()
            context = assemble(pool, chunks, case, config)
            context_ms = (perf_counter() - context_started) * 1000
            total_ms = (perf_counter() - started) * 1000
            score = score_evidence(case, pool[: config["top_k"]], context)
            ids = [h.chunk.id for h in pool]
            previous = reference.setdefault((name, case.case_id), ids)
            expected = replay[case.case_id]["all" if triggered else "none"]
            row = {
                "id": case.case_id,
                "group_id": case.group_id,
                "strategy": name,
                "repeat": repeat,
                "order": order,
                "triggered": triggered,
                "scored_pairs": len(before) if triggered else 0,
                "total_ms": total_ms,
                "retrieval_ms": retrieval_ms,
                "gate_ms": gate_ms,
                "context_ms": context_ms,
                "rerank_ms": trace["elapsed_ms"] if trace else 0.0,
                "truncated_pairs": trace["truncated_count"] if trace else 0,
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
                "measurement_segment": 1,
            }
            trials.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            if name == order[-1]:
                print(
                    f"{len(trials)}/{len(schedule)} repeat {repeat + 1} {case.case_id}", flush=True
                )
    sample = {(r["id"], r["strategy"]): r for r in trials if r["repeat"] == 0}
    per_case = [
        {
            "id": c.case_id,
            "group_id": c.group_id,
            "question": c.question,
            "filters": c.filters,
            "gold_evidence": list(c.evidence),
            "signals": replay[c.case_id]["signals"],
            "strategies": {n: sample[c.case_id, n]["evidence"] for n in STRATEGIES},
        }
        for c in cases
    ]
    summary = {
        n: {
            "n": len(cases),
            "top_complete": sum(r["strategies"][n]["top_complete"] for r in per_case),
            "context_complete": sum(r["strategies"][n]["context_complete"] for r in per_case),
            "triggered_queries": sum(sample[c.case_id, n]["triggered"] for c in cases),
        }
        for n in STRATEGIES
    }
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
    verify_hashes(selection["lock"])
    real = {
        "created_at": timestamp(),
        "scope": "real_local_cpu_retrieval_only_development",
        "configuration": config,
        "selected_policy": selection["selected"]["policy"],
        "selection_sha256": selection_hash,
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
        "measurement_segments": [
            {
                "segment": 0,
                "trials": len(prefix),
                "startup": old_runtime["startup"],
                "environment": old_runtime["environment"],
            },
            {
                "segment": 1,
                "trials": len(trials) - len(prefix),
                "startup": startup,
                "environment": environment,
            },
        ],
        "execution": {
            "trials": len(trials),
            "paid_api_calls": 0,
            "generated_answers": 0,
            "held_out_cases": 0,
            "warmups_excluded": 6,
            "measurement_segments": 2,
            **{
                key: sum(not r[field] for r in trials)
                for key, field in [
                    ("unstable_rankings", "stable_ranking"),
                    ("replay_ranking_mismatches", "same_ranking_as_replay"),
                    ("replay_coverage_mismatches", "same_coverage_as_replay"),
                    ("gate_decision_mismatches", "gate_matches_frozen_selection"),
                ]
            },
        },
        "protected_inputs": verify_hashes(read_json(args.run_dir / "protected-before.json")),
        "trials_sha256": sha256_file(args.output_dir / "real.trials.jsonl"),
    }
    write_json(args.output_dir / "real.json", real)
    print(json.dumps({"summary": summary, "execution": real["execution"]}), flush=True)


if __name__ == "__main__":
    main()
