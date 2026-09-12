"""Run a fixed development-only ablation matrix; no test-set tuning."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Hashing only, without model API calls")
    args = parser.parse_args()
    env = os.environ.copy()
    if args.demo:
        env.update(
            EMBEDDING_PROVIDER="hashing", LLM_PROVIDER="extractive", DATA_DIR="data/demo-index"
        )
    summaries = []
    for strategy in ("window", "sections"):
        for size in (300, 500, 800):
            name = f"support-{'hashing' if args.demo else 'configured'}-{strategy}-{size}"
            command = [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "scripts.evaluate",
                "--documents",
                "evaluation/support_corpus",
                "--questions",
                "evaluation/questions.support.candidate.jsonl",
                "--split",
                "development",
                "--chunking-strategy",
                strategy,
                "--chunk-size",
                str(size),
                "--chunk-overlap",
                "80",
                "--top-k",
                "1",
                "3",
                "5",
                "10",
                "--report-name",
                name,
            ]
            result = subprocess.run(
                command, env=env, capture_output=True, text=True, encoding="utf-8"
            )
            if result.returncode:
                # Provider errors can contain request details; don't echo them into reports.
                raise RuntimeError(
                    f"Experiment failed: {name}. Check credentials, corpus or local dependencies."
                )
            path = Path("evaluation/reports") / f"{name}.json"
            report = json.loads(path.read_text(encoding="utf-8"))
            summaries.append(
                {
                    "report": path.as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "configuration": report["configuration"],
                    "provenance": report["provenance"],
                    "metrics_at_5": {
                        method: {
                            key: value for key, value in results["5"].items() if key != "details"
                        }
                        for method, results in report["results"].items()
                    },
                }
            )
            print(f"Finished {name}", flush=True)
    path = Path("evaluation/reports") / (
        "support-matrix-hashing.json" if args.demo else "support-matrix-configured.json"
    )
    path.write_text(
        json.dumps(
            {"scope": "unreviewed development diagnostics; not resume evidence", "runs": summaries},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(summaries)} runs to {path}")


if __name__ == "__main__":
    main()
