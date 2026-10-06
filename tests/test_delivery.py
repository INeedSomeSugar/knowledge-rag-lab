from concurrent.futures import ThreadPoolExecutor
import json
import logging
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

import app.main as main_module
from app.observability import request_id_context
from scripts import benchmark_http
from scripts.verify_local import child_runtime
from tests.test_service import build_service


def test_readiness_tracks_loaded_index_without_model_request(tmp_path, monkeypatch):
    service = build_service(tmp_path)
    monkeypatch.setattr(main_module, "service", service)
    client = TestClient(main_module.app)
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    created = service.ingest("guide.md", "跨域设置需要正确指定允许的来源。")
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["index_revision"] == service.index_revision
    assert ready.json()["model_connectivity"] == "not_checked"
    service.delete_document(created["document_id"])
    assert client.get("/ready").status_code == 503


def test_concurrent_http_ids_match_rag_traces_and_do_not_leak(tmp_path, monkeypatch):
    service = build_service(tmp_path)
    service.ingest("guide.md", "跨域设置需要正确指定允许的来源。")
    monkeypatch.setattr(main_module, "service", service)

    def request(_):
        response = TestClient(main_module.app).post(
            "/api/v1/chat",
            json={"question": "跨域设置", "top_k": 1},
            headers={"X-Request-ID": "untrusted-client-id"},
        )
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == response.json()["trace"]["request_id"]
        return response.headers["X-Request-ID"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(request, range(8)))
    assert len(set(ids)) == 8
    assert "untrusted-client-id" not in ids
    assert request_id_context.get() is None


def test_request_log_omits_body_query_headers_and_unmatched_path(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(main_module, "service", build_service(tmp_path))
    caplog.set_level(logging.INFO, logger="rag.requests")
    client = TestClient(main_module.app)
    client.post(
        "/api/v1/chat?secret=PRIVATE_QUERY",
        json={"question": "PRIVATE_BODY", "top_k": 1},
        headers={"Authorization": "PRIVATE_HEADER"},
    )
    client.get("/PRIVATE_PATH")
    logs = [json.loads(row.message) for row in caplog.records if row.name == "rag.requests"]
    assert len(logs) == 2
    assert logs[0]["route"] == "/api/v1/chat"
    assert logs[1]["route"] == "unmatched"
    assert "PRIVATE" not in json.dumps(logs)


def test_unhandled_error_returns_correlated_generic_response(tmp_path, monkeypatch, caplog):
    service = build_service(tmp_path)
    monkeypatch.setattr(main_module, "service", service)

    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE_PROVIDER_DETAILS")

    monkeypatch.setattr(service, "answer", fail)
    caplog.set_level(logging.INFO, logger="rag.requests")
    response = TestClient(main_module.app).post("/api/v1/chat", json={"question": "跨域配置"})
    assert response.status_code == 500
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert "PRIVATE" not in response.text
    logs = [json.loads(row.message) for row in caplog.records if row.name == "rag.requests"]
    assert logs[0]["error_type"] == "RuntimeError"
    assert "PRIVATE" not in json.dumps(logs)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com",
        "http://user:pass@localhost",
        "http://localhost/?token=x",
        "http://localhost/#x",
        "http://localhost/api",
    ],
)
def test_load_tool_rejects_nonlocal_or_credentialed_targets(url):
    with pytest.raises(ValueError):
        benchmark_http.validate_base_url(url)


def test_load_tool_stops_before_chat_when_real_model_mode_is_detected(monkeypatch):
    paths = []

    def request(base, path, **kwargs):
        paths.append(path)
        return {
            "embedding_provider": "openai_compatible",
            "llm_provider": "openai_compatible",
        }, "id"

    monkeypatch.setattr(benchmark_http, "request_json", request)
    with pytest.raises(ValueError, match="拒绝调用真实模型"):
        benchmark_http.run_workload("http://127.0.0.1:8000")
    assert paths == ["/health"]


def test_load_tool_counts_application_failures_even_when_http_succeeds(monkeypatch):
    monkeypatch.setattr(benchmark_http, "inspect_demo", lambda *args: ({}, {}))
    monkeypatch.setattr(
        benchmark_http,
        "request_json",
        lambda *args, **kwargs: ({"status": "answered", "trace": {"request_id": "id"}}, "id"),
    )
    report = benchmark_http.run_workload(
        "http://localhost:8000", requests=3, concurrency=1, warmup=0
    )
    assert report["summary"]["failure_count"] == 3
    assert report["summary"]["success_count"] == 0


def test_verification_subprocess_owns_interpreter_and_retains_venv():
    executable, environment = child_runtime()
    process = subprocess.Popen(
        [executable, "-c", "import json,os,sys; print(json.dumps([os.getpid(),sys.prefix]))"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        stdout, _ = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert process.returncode == 0
    pid, prefix = json.loads(stdout)
    assert pid == process.pid
    assert prefix == sys.prefix


def test_load_tool_rejects_http_redirects():
    assert (
        benchmark_http.NoRedirect().redirect_request(None, None, 302, "", {}, "http://remote")
        is None
    )
