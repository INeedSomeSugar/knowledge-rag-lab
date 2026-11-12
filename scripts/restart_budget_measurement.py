"""Retain an interrupted prefix and reuse the exact gate for a NEW measurement."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from app.local_embeddings import sha256_file
from scripts.experiment_budget_reranking import read_json, verify_hashes, write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--source-run", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    args = p.parse_args()
    if args.output_dir.exists() or args.run_dir.exists():
        p.error("fresh output and runtime directories required")
    if (args.source / "real.json").exists():
        p.error("source is a completed measurement, not an interrupted prefix")
    source_selection = args.source / "selection.json"
    if sha256_file(source_selection) != (args.source / "selection.sha256").read_text().strip():
        p.error("selection hash mismatch")
    verify_hashes(read_json(source_selection)["lock"])
    import psutil

    original_runtime = read_json(args.source_run / "runtime-start.json")
    if psutil.pid_exists(original_runtime["pid"]):
        p.error("original process ID still exists; inspect before starting duplicate work")
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
    partial = args.source / "real.trials.jsonl"
    trials = [json.loads(line) for line in partial.read_text(encoding="utf-8").splitlines()]
    write_json(
        args.run_dir / "recovery.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "original_output": str(args.source),
            "original_runtime": str(args.source_run),
            "partial_trials": len(trials),
            "partial_sha256": sha256_file(partial),
            "original_pid_absent": True,
            "cause": "Process absent after environment/tool restart; original exit status unavailable; exact cause unverified",
            "selected_policy_sha256": sha256_file(source_selection),
            "retuned": False,
            "timing_policy": "Original prefix excluded. New measurement starts from scratch, with new cache and warmups.",
            "generator_sha256": sha256_file(Path(__file__)),
        },
    )
    print(
        json.dumps(
            {
                "preserved_partial_trials": len(trials),
                "retuned": False,
                "new_output": str(args.output_dir),
            }
        )
    )


if __name__ == "__main__":
    main()
