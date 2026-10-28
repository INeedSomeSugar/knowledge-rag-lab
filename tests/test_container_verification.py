import json
from types import SimpleNamespace

import pytest

from scripts import verify_container


def fake_docker(monkeypatch, env=None, host_ip="127.0.0.1"):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    seen = []
    inspect_count = 0

    def run(args, **kwargs):
        nonlocal inspect_count
        seen.append(args)
        if args[1:2] == ["--host"]:
            args = [args[0], *args[3:]]
        output = ""
        if args[:2] == ["docker", "context"]:
            output = json.dumps("unix:///var/run/docker.sock")
        elif args[:2] == ["docker", "info"]:
            output = "linux"
        elif args[:2] == ["docker", "inspect"]:
            inspect_count += 1
            output = json.dumps(
                [
                    {
                        "NetworkSettings": {
                            "Ports": {
                                "8000/tcp": [
                                    {
                                        "HostIp": host_ip,
                                        "HostPort": "51001" if inspect_count == 1 else "51002",
                                    }
                                ]
                            }
                        },
                        "HostConfig": {"ReadonlyRootfs": True},
                        "Config": {"User": "10001:10001", "Env": env or []},
                        "State": {"Health": {"Status": "healthy"}},
                        "Image": "sha256:fixture",
                    }
                ]
            )
        elif "compose" in args and "ps" in args:
            output = "fixture-container"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(verify_container.subprocess, "run", run)
    return seen


def test_restart_uses_new_ephemeral_port_and_always_cleans_own_project(monkeypatch, tmp_path):
    seen = fake_docker(monkeypatch)
    monkeypatch.setattr(
        verify_container,
        "run_workload",
        lambda url, **kwargs: {
            "summary": {"failure_count": 0},
            "readiness": {"index_revision": "fixed"},
        },
    )
    urls = []

    def request(url, *args, **kwargs):
        urls.append(url)
        return {"status": "ready", "index_revision": "fixed"}, "request-id"

    monkeypatch.setattr(verify_container, "request_json", request)
    report = verify_container.verify(tmp_path / "report.json", "fixture-image")
    assert report["passed"] and urls == ["http://127.0.0.1:51002"]
    down = next(args for args in seen if "down" in args)
    assert report["project"].startswith("rag-verify-") and report["project"] in down
    assert "--volumes" in down


@pytest.mark.parametrize("failure", ["credentials", "public_port", "http"])
def test_validation_failure_still_cleans_and_preserves_failed_report(
    monkeypatch, tmp_path, failure
):
    secret = "fixture-sensitive-value"
    seen = fake_docker(
        monkeypatch,
        env=["LLM_API_KEY=" + secret] if failure == "credentials" else [],
        host_ip="0.0.0.0" if failure == "public_port" else "127.0.0.1",
    )

    def fail_http(*args, **kwargs):
        raise RuntimeError("fixture_http_failure")

    monkeypatch.setattr(verify_container, "run_workload", fail_http)
    output = tmp_path / "failed.json"
    report = verify_container.verify(output, "fixture-image")
    assert not report["passed"] and report["checks"][-1]["passed"]
    assert any("down" in args for args in seen)
    assert secret not in output.read_text("utf-8")


def test_remote_docker_endpoint_is_rejected_before_contact(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "ssh://remote.example")
    monkeypatch.setattr(
        verify_container.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("must not contact remote daemon or attempt remote cleanup"),
    )
    report = verify_container.verify(tmp_path / "remote.json", "fixture-image")
    assert not report["passed"] and report["error"] == "local_docker_endpoint"


def test_log_timeout_cannot_skip_resource_cleanup(monkeypatch, tmp_path):
    seen = fake_docker(monkeypatch)
    original = verify_container.subprocess.run

    def run(args, **kwargs):
        if "logs" in args:
            raise verify_container.subprocess.TimeoutExpired(args, 60)
        return original(args, **kwargs)

    monkeypatch.setattr(verify_container.subprocess, "run", run)

    def fail_http(*args, **kwargs):
        raise RuntimeError("fixture_http_failure")

    monkeypatch.setattr(verify_container, "run_workload", fail_http)
    report = verify_container.verify(tmp_path / "failed.json", "fixture-image")
    assert not report["passed"] and report["checks"][-1]["passed"]
    assert report["log_capture_error_type"] == "TimeoutExpired"
    assert any("down" in args for args in seen)
