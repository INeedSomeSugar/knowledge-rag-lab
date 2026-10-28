"""Build and verify an ephemeral loopback demo container, retaining reproducible evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import yaml

from scripts.benchmark_http import request_json, run_workload, write_report


def verify(output: Path, existing_image: str | None = None) -> dict:
    if output.exists():
        raise ValueError("report already exists; use a new path")
    root = Path(__file__).resolve().parents[1]
    key = uuid.uuid4().hex
    project = "rag-verify-" + key
    directory = root / "work" / "container-verification" / key
    directory.mkdir(parents=True)
    configuration = yaml.safe_load((root / "compose.yaml").read_text("utf-8"))
    service = configuration["services"]["rag"]
    service["build"] = str(root)
    service["ports"] = ["127.0.0.1::8000"]
    service["command"] = [
        "python",
        "-m",
        "scripts.serve",
        "--demo",
        "--bootstrap",
        "--host",
        "0.0.0.0",
        "--data-dir",
        "/data",
    ]
    if existing_image:
        service["image"] = existing_image
    compose = directory / "compose.yaml"
    compose.write_text(yaml.safe_dump(configuration, sort_keys=False), "utf-8")
    prefix = ["docker", "compose", "-p", project, "-f", str(compose)]
    endpoint = None
    provisioning_attempted = False
    report = {
        "schema_version": "rag.container-validation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": project,
        "checks": [],
        "passed": False,
        "scope": "Local Linux demo container; no generation API, answer scoring, or remote CI.",
        "run_directory": str(directory),
        "existing_image": existing_image,
    }

    def command(args, timeout=60, check=True):
        if endpoint:
            args = [args[0], "--host", endpoint, *args[1:]]
        result = subprocess.run(
            args,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        if check and result.returncode:
            raise RuntimeError("container_command_failed: " + " ".join(args[:5]))
        return result

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        if not passed:
            raise RuntimeError(name)

    try:
        endpoint = os.environ.get("DOCKER_HOST")
        if not endpoint:
            context = command(
                ["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"]
            )
            endpoint = json.loads(context.stdout)
        check(
            "local_docker_endpoint",
            isinstance(endpoint, str) and endpoint.startswith(("npipe://", "unix://")),
        )
        report["docker_endpoint"] = endpoint
        engine = command(["docker", "info", "--format", "{{.OSType}}"])
        check("linux_engine", engine.stdout.strip() == "linux")
        if not existing_image:
            built = command([*prefix, "build"], timeout=900, check=False)
            (directory / "build.log").write_text(built.stdout + built.stderr, "utf-8")
            check("image_build", built.returncode == 0)
        provisioning_attempted = True
        command([*prefix, "up", "-d", "--no-build", "--wait", "--wait-timeout", "90"], timeout=120)
        container_id = command([*prefix, "ps", "-q", "rag"]).stdout.strip()
        inspected = json.loads(command(["docker", "inspect", container_id]).stdout)[0]
        bindings = inspected["NetworkSettings"]["Ports"]["8000/tcp"]
        check(
            "loopback_only_publish",
            bool(bindings) and all(item["HostIp"] == "127.0.0.1" for item in bindings),
        )
        check("read_only_root", inspected["HostConfig"]["ReadonlyRootfs"] is True)
        check("non_root_user", inspected["Config"]["User"] == "10001:10001")
        check("healthy", inspected["State"]["Health"]["Status"] == "healthy")
        environment_names = [item.split("=", 1)[0] for item in inspected["Config"]["Env"]]
        check("no_api_credentials", not any("API_KEY" in name for name in environment_names))
        report["container"] = {
            "id": container_id,
            "image_id": inspected["Image"],
            "user": inspected["Config"]["User"],
            "ports": bindings,
            "read_only_root": True,
            "environment_names": environment_names,
        }
        dependencies = command([*prefix, "exec", "-T", "rag", "python", "-m", "pip", "freeze"])
        (directory / "dependencies.txt").write_text(dependencies.stdout, "utf-8")
        command([*prefix, "exec", "-T", "rag", "python", "-m", "pip", "check"])
        check("container_dependency_consistency", True)
        base_url = "http://127.0.0.1:" + bindings[0]["HostPort"]
        workload = run_workload(base_url, requests=12, concurrency=3)
        report["http"] = workload
        check("http_12_business_checks", workload["summary"]["failure_count"] == 0)
        revision = workload["readiness"]["index_revision"]
        command([*prefix, "restart", "rag"], timeout=45)
        restarted = json.loads(command(["docker", "inspect", container_id]).stdout)[0]
        restarted_bindings = restarted["NetworkSettings"]["Ports"]["8000/tcp"]
        check(
            "restart_loopback_only_publish",
            bool(restarted_bindings)
            and all(item["HostIp"] == "127.0.0.1" for item in restarted_bindings),
        )
        report["after_restart_bindings"] = restarted_bindings
        # Docker may allocate a new ephemeral host port when restarting the same container.
        base_url = "http://127.0.0.1:" + restarted_bindings[0]["HostPort"]
        ready = None
        for _ in range(45):
            try:
                ready, _ = request_json(base_url, "/ready", timeout=2)
                if ready.get("status") == "ready":
                    break
            except (OSError, ValueError):
                pass
            time.sleep(1)
        report["after_restart"] = ready
        check(
            "restart_preserves_ready_index", bool(ready) and ready.get("index_revision") == revision
        )
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        # All raised messages are local constants or fixed command names, never provider errors.
        report["error"] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
    finally:
        try:
            clean = True
            if provisioning_attempted:
                try:
                    logs = command([*prefix, "logs", "--no-color"], check=False)
                    (directory / "container.log").write_text(logs.stdout + logs.stderr, "utf-8")
                except Exception as exc:
                    # Optional diagnostics must never prevent cleanup of our temporary resources.
                    report["log_capture_error_type"] = type(exc).__name__
                removed = command([*prefix, "down", "--volumes"], timeout=60, check=False)
                containers = command(
                    [
                        "docker",
                        "ps",
                        "-aq",
                        "--filter",
                        "label=com.docker.compose.project=" + project,
                    ]
                )
                volumes = command(
                    [
                        "docker",
                        "volume",
                        "ls",
                        "-q",
                        "--filter",
                        "label=com.docker.compose.project=" + project,
                    ]
                )
                clean = (
                    removed.returncode == 0
                    and not containers.stdout.strip()
                    and not volumes.stdout.strip()
                )
            report["checks"].append({"name": "ephemeral_project_cleanup", "passed": clean})
        except Exception as exc:
            report["cleanup_error_type"] = type(exc).__name__
            report["checks"].append({"name": "ephemeral_project_cleanup", "passed": False})
        report["source_sha256"] = {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (
                "Dockerfile",
                "compose.yaml",
                ".dockerignore",
                "pyproject.toml",
                "scripts/verify_container.py",
            )
        }
        report["artifact_sha256"] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.iterdir())
            if path.is_file()
        }
        report["passed"] = not report.get("error_type") and all(
            row["passed"] for row in report["checks"]
        )
        write_report(report, output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--existing-image", help="Use an already built local image; skip build")
    args = parser.parse_args()
    report = verify(args.output, args.existing_image)
    print(
        json.dumps(
            {"passed": report["passed"], "checks": report["checks"], "report": str(args.output)},
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
