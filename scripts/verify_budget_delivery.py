"""Check the completed budget experiment, review states, links and test receipt."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jsonl(path):
    return [json.loads(s) for s in Path(path).read_text(encoding="utf-8").splitlines() if s]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--experiment-dir", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--review-dir", type=Path, required=True)
    p.add_argument("--junit", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error("verification output must be new")
    real = load(args.experiment_dir / "real.json")
    analysis = args.experiment_dir / "analysis"
    rows = jsonl(args.experiment_dir / "real.trials.jsonl")
    assert len(rows) == real["execution"]["trials"] == 369
    assert real["execution"]["paid_api_calls"] == real["execution"]["held_out_cases"] == 0
    protected = load(args.run_dir / "protected-before.json")
    changed = [f for f, h in protected.items() if not Path(f).is_file() or digest(f) != h]
    assert not changed, changed
    selection = args.experiment_dir / "selection.json"
    assert digest(selection) == real["selection_sha256"]
    locked = load(selection)["lock"]
    assert all(digest(f) == h for f, h in locked.items())
    manifest = load(args.review_dir / "manifest.json")
    assert all(digest(args.review_dir / f) == h for f, h in manifest["files"].items())
    candidate = jsonl(args.review_dir / "questions.candidate.jsonl")
    checks = jsonl(args.review_dir / "assistant-checks.jsonl")
    human = jsonl(args.review_dir / "human-review.pending.jsonl")
    assert len(candidate) == len(checks) == len(human) == 12
    assert {r["id"] for r in candidate} == {r["id"] for r in checks} == {r["id"] for r in human}
    for row, check, record in zip(candidate, checks, human, strict=True):
        assert row["review_status"] == "unreviewed" and row["split"] == "test"
        assert check["status"] == "assistant_checked" and check["human_verified"] is False
        assert record["status"] == "pending" and record["reviewed_by"] is None
        assert record["reviewed_at"] is None
        assert all(
            record[k] is None
            for k in (
                "question_and_scope",
                "answer_and_behavior",
                "evidence_sufficient_and_complete",
            )
        )
        for anchor in row["evidence"]:
            text = (args.review_dir / "sources" / anchor["source"]).read_text(encoding="utf-8")
            assert text[anchor["start_char"] : anchor["end_char"]] == anchor["quote"]
    junit_root = ET.parse(args.junit).getroot()
    suites = junit_root.findall(".//testsuite")
    tests = {
        k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")
    }
    assert tests["tests"] > 220 and not any(tests[k] for k in ("failures", "errors", "skipped"))
    local_model_tests = {
        "test_real_local_e5_vectors_prefixes_truncation_and_cached_reload",
        "test_real_local_bge_scoring_and_identity",
    }
    assert local_model_tests <= {c.get("name") for c in junit_root.findall(".//testcase")}
    link_files = [
        Path("README.md"),
        Path("docs/BUDGET_RERANKING_20261106.md"),
        *analysis.glob("*.md"),
        args.review_dir / "REVIEW.md",
    ]
    checked_links = 0
    for file in link_files:
        for target in re.findall(r"\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
            target = target.strip("<>").split("#")[0]
            if not target or re.match(r"[a-z]+://", target):
                continue
            assert (file.parent / target).exists(), (str(file), target)
            checked_links += 1
    images = {}
    for name in ("summary", "frontier", "latency-cdf"):
        png = analysis / (name + ".png")
        raw = png.read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", raw[16:24])
        assert width > 800 and height > 400
        svg = analysis / (name + ".svg")
        assert len(list(ET.parse(svg).getroot().iter())) > 50
        images[name] = {"width": width, "height": height}
    assert (
        len(
            [
                s
                for s in (analysis / "RESUME.md").read_text(encoding="utf-8").splitlines()
                if s.startswith("- ")
            ]
        )
        == 2
    )
    commands = [
        [sys.executable, "-m", "ruff", "check", "--no-cache", "app", "scripts", "tests"],
        [sys.executable, "-m", "pip", "check"],
        ["git", "diff", "--check"],
    ]
    static_checks = []
    for command in commands:
        checked = subprocess.run(command, text=True, encoding="utf-8", capture_output=True)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        static_checks.append(
            {
                "command": command,
                "exit_code": checked.returncode,
                "stdout": checked.stdout,
                "stderr": checked.stderr,
            }
        )
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "completed development retrieval experiment and local delivery",
        "protected_files_checked": len(protected),
        "changed_protected_files": changed,
        "selected_gate_sha256": digest(selection),
        "raw_trials": len(rows),
        "human_review_states": {"candidates": 12, "assistant_checked": 12, "human_verified": 0},
        "junit": {"path": str(args.junit), "sha256": digest(args.junit), **tests},
        "local_model_tests": sorted(local_model_tests),
        "static_checks": static_checks,
        "local_links_checked": checked_links,
        "plot_dimensions": images,
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "git_status": subprocess.check_output(["git", "status", "--short"], text=True),
        "artifacts_sha256": {
            str(f): digest(f)
            for folder in (args.experiment_dir, args.review_dir)
            for f in sorted(folder.rglob("*"))
            if f.is_file()
        },
        "new_code_sha256": {
            str(f): digest(f)
            for f in [
                *Path("scripts").glob("*budget*.py"),
                Path("scripts/rasterize_budget_plots.cjs"),
                *Path("app").glob("budget*.py"),
                Path("tests/test_budget_reranking.py"),
                Path("PROJECT_CONTEXT.md"),
                Path("README.md"),
                Path("pyproject.toml"),
            ]
        },
    }
    with args.output.open("x", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "protected": len(protected),
                "tests": tests,
                "links": checked_links,
                "candidate_human_verified": 0,
                "raw_trials": len(rows),
            }
        )
    )


if __name__ == "__main__":
    main()
