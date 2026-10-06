"""Start an isolated demo subprocess, exercise actual HTTP, then stop that process."""

import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

from scripts.benchmark_http import inspect_demo, request_json, run_workload, write_report


def child_runtime() -> tuple[str, dict[str, str]]:
    environment = os.environ.copy()
    executable = sys.executable
    # Match CPython multiprocessing's bpo-35797 Windows venv launcher handling.
    # Launch the real interpreter while retaining venv package resolution, so the
    # owned Popen handle refers to the server itself rather than a redirector.
    if os.name == "nt" and sys.executable != sys._base_executable:
        executable = sys._base_executable
        environment["__PYVENV_LAUNCHER__"] = sys.executable
    environment["RAG_INSTANCE_ID"] = uuid.uuid4().hex
    return executable, environment


def verify(output: Path, requests: int = 24, concurrency: int = 4,
           context_policy: str = "neighbors") -> dict:
    if context_policy not in {"neighbors", "merged_neighbors"}:
        raise ValueError("不支持的上下文策略")
    if output.exists():
        raise ValueError("报告已存在，请使用新输出路径")
    root = Path(__file__).resolve().parent.parent
    work = (root / "work" / "http-verification" / uuid.uuid4().hex).resolve()
    if not work.is_relative_to(root):
        raise ValueError("临时索引目录必须位于仓库内")
    work.mkdir(parents=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log_path = work / "server.log"
    executable, environment = child_runtime()
    instance = environment["RAG_INSTANCE_ID"]
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                executable,
                "-X",
                "utf8",
                "-m",
                "scripts.serve",
                "--demo",
                "--bootstrap",
                "--port",
                str(port),
                "--data-dir",
                str(work / "index"),
                "--context-policy",
                context_policy,
            ],
            cwd=root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            base_url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"演示服务提前退出；查看 {log_path}")
                try:
                    health, _ = inspect_demo(base_url, expected_instance=instance)
                    if health.get("context_policy") != context_policy:
                        raise ValueError("服务上下文策略与验证配置不一致")
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"演示服务启动检查超时；查看 {log_path}") from None
                    time.sleep(0.15)
            report = run_workload(
                base_url, requests=requests, concurrency=concurrency, expected_instance=instance
            )
            report["server_log"] = log_path.relative_to(root).as_posix()
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    report["server_process_stopped"] = process.poll() is not None
    try:
        health, _ = request_json(base_url, "/health", timeout=1)
    except Exception:
        health = {}
    if health.get("instance_id") == instance:
        raise RuntimeError("本次服务仍在运行，不能报告清理成功")
    write_report(report, output)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("evaluation/reports/local-http.json"))
    parser.add_argument("--requests", type=int, default=24)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--context-policy", choices=["neighbors", "merged_neighbors"],
                        default="neighbors")
    args = parser.parse_args()
    report = verify(args.output, args.requests, args.concurrency, args.context_policy)
    print(
        f"HTTP: {report['summary']['success_count']}/{args.requests}; stopped={report['server_process_stopped']}"
    )
    print(f"Report: {args.output}")
    if report["summary"]["failure_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
