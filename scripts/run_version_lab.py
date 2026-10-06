"""Prepare isolated pinned environments and record actual fixed-case behavior."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parent.parent
CASE_SCRIPT = ROOT / "scripts" / "version_cases.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def environment_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(command: list[str], *, timeout: int = 30, env: dict | None = None):
    return subprocess.run(
        command, check=True, capture_output=True, text=True, encoding="utf-8",
        timeout=timeout, env=env, cwd=ROOT,
    ).stdout


def validate_observation(result: dict, target: str) -> list[dict]:
    if result["fastapi"] != target:
        raise ValueError("Runner used the wrong FastAPI environment")
    checks = []
    for case in result["cases"]:
        owned = case["case_id"].endswith("_owned")
        expected = "completed" if owned or target == "0.118.0" else "failed"
        error = case["error"]
        matched = case["execution_status"] == expected
        if expected == "failed":
            matched = matched and error == {"type": "RuntimeError", "message": "resource_closed"}
        else:
            matched = matched and case["response_completed"] and error is None
        events = case.get("events", [])
        owner = ("stream" if case["case_id"].startswith("stream") else "task") if owned else (
            "dependency"
        )
        read_event = f"{owner}:read:{'closed' if expected == 'failed' else 'open'}"
        close_event = f"{owner}:close"
        if read_event not in events or close_event not in events:
            matched = False
        elif expected == "failed":
            matched = matched and events.index(close_event) < events.index(read_event)
        else:
            matched = matched and events.index(read_event) < events.index(close_event)
        checks.append({
            "case_id": case["case_id"], "expected_status": expected,
            "observation_matches_expectation": matched,
        })
    if {c["case_id"] for c in checks} != {
        "stream_borrowed", "stream_owned", "background_borrowed", "background_owned"
    } or len(checks) != 4:
        raise ValueError("Runner did not execute exactly the four fixed cases")
    return checks


def render_markdown(report: dict, raw_sha256: str) -> str:
    lines = [
        "# FastAPI 固定版本行为实验", "",
        f"- 原始 JSON SHA-256：`{raw_sha256}`",
        f"- 案例代码 SHA-256：`{report['case_script_sha256']}`",
        f"- 文档清单 SHA-256：`{report['corpus_manifest_sha256']}`",
        "- 范围：模拟资源、直接 ASGI 调用；未运行数据库、网络服务器或生成模型。", "",
        "| FastAPI | 案例 | 实际执行 | 响应已完成 | 错误 | 符合预期 |",
        "|---|---|---|---|---|---|",
    ]
    for entry in report["runs"]:
        checks = validate_observation(entry["observation"], entry["target_version"])
        for case, check in zip(entry["observation"]["cases"], checks, strict=True):
            error = case["error"]["message"] if case["error"] else "无"
            lines.append(
                f"| {entry['target_version']} | {case['case_id']} | {case['execution_status']} "
                f"| {case['response_completed']} | {error} "
                f"| {check['observation_matches_expectation']} |"
            )
    lines.extend([
        "", "所有案例均可能先发送 HTTP 200；状态码不能替代任务完成与事件顺序检查。",
        "执行失败与符合预期分别记录。此表不代表 RAG 回答准确率或真实数据库验证。", "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-environments", action="store_true",
                        help="Explicitly allow pip downloads into isolated work environments")
    parser.add_argument("--output", type=Path,
                        default=Path("evaluation/reports/version-behavior-v09.json"))
    parser.add_argument("--summarize-only", action="store_true",
                        help="Read an existing raw report and generate its Markdown without rerunning")
    args = parser.parse_args()
    markdown = args.output.with_suffix(".md")
    if args.summarize_only:
        if args.prepare_environments:
            parser.error("Summary mode cannot prepare or execute environments")
        report = json.loads(args.output.read_text(encoding="utf-8"))
        if markdown.exists():
            parser.error("Markdown exists; historical summary cannot be overwritten")
        if report["case_script_sha256"] != sha256(CASE_SCRIPT):
            parser.error("Report case fingerprint is stale")
        with markdown.open("x", encoding="utf-8", newline="\n") as file:
            file.write(render_markdown(report, sha256(args.output)))
        print(f"Saved derived summary to {markdown}")
        return
    if args.output.exists():
        parser.error("Report exists; choose a new output to preserve raw history")
    if markdown.exists():
        parser.error("Markdown output already exists")
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    versions = config["tool"]["version-lab"]["versions"]
    requirements = config["project"]["optional-dependencies"]["version-lab"]
    report = {
        "schema_version": "1.0", "kind": "fixed_asgi_version_behavior",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_script_sha256": sha256(CASE_SCRIPT),
        "corpus_manifest_sha256": sha256(ROOT / "evaluation/version_corpus/manifest.json"),
        "versions": versions, "common_requirements": requirements, "runs": [],
        "scope": "In-process ASGI with a synthetic resource; no database, model or user code",
    }
    clean_env = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "HOME"}
    }
    clean_env["PYTHONUTF8"] = "1"
    for target in versions:
        environment = ROOT / "work" / "version-lab" / f"fastapi-{target}"
        interpreter = environment_python(environment)
        if args.prepare_environments:
            if not interpreter.exists():
                run([sys.executable, "-m", "venv", str(environment)], timeout=60)
            run([str(interpreter), "-m", "pip", "install", "--disable-pip-version-check",
                 f"fastapi=={target}", *requirements], timeout=180)
        if not interpreter.exists():
            parser.error("Environment missing; run with --prepare-environments first")
        run([str(interpreter), "-m", "pip", "check"])
        packages = json.loads(run([str(interpreter), "-m", "pip", "list", "--format=json"]))
        installed = {item["name"].lower(): item["version"] for item in packages}
        for requirement in [f"fastapi=={target}", *requirements]:
            name, pinned = requirement.split("==")
            if installed.get(name.lower()) != pinned:
                raise ValueError("Pinned runtime dependencies differ; prepare environments again")
        output = run([str(interpreter), "-I", str(CASE_SCRIPT)], env=clean_env)
        observation = json.loads(output)
        checks = validate_observation(observation, target)
        report["runs"].append({
            "target_version": target, "dependencies": packages,
            "observation": observation, "checks": checks,
        })
        print(f"FastAPI {target}: executed {len(checks)} fixed cases", flush=True)
    report["all_observations_match"] = all(
        check["observation_matches_expectation"]
        for entry in report["runs"] for check in entry["checks"]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print(f"Saved raw observations to {args.output}")
    with markdown.open("x", encoding="utf-8", newline="\n") as file:
        file.write(render_markdown(report, sha256(args.output)))
    if not report["all_observations_match"]:
        raise SystemExit("Unexpected behavior recorded; inspect raw report before making claims")


if __name__ == "__main__":
    main()
