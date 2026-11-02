"""Real loopback HTTP/SSE with a synthetic streamed upstream; zero paid API calls."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
from threading import Thread
import time

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import uvicorn


@contextmanager
def serve(application):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(application, log_level="error", lifespan="off"))
    thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("probe_server_start_failed")
            time.sleep(0.02)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        if thread.is_alive():
            raise RuntimeError("probe_server_cleanup_failed")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists() or args.run_dir.exists():
        p.error("output/run-dir must be new")
    os.environ.update(
        EMBEDDING_PROVIDER="hashing",
        LLM_PROVIDER="extractive",
        DATA_DIR=str(args.run_dir / "index"),
    )
    import app.main as main_module
    from app.generation import OpenAICompatibleGenerator

    service = main_module.service
    evidence = "SSE 使用 text/event-stream 媒体类型。"
    for version in ("1.0.0", "2.0.0"):
        service.ingest("probe.md", evidence, {"product": "probe", "version": version})
    raw = json.dumps(
        {
            "status": "answered",
            "claims": [
                {
                    "text": "SSE 的媒体类型是 text/event-stream。",
                    "evidence": [{"citation": 1, "quote": evidence}],
                }
            ],
        },
        ensure_ascii=False,
    )
    upstream = FastAPI()

    @upstream.post("/v1/chat/completions")
    def completion():
        def body():
            for offset in range(0, len(raw), 12):
                payload = {
                    "id": "probe",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "synthetic-upstream",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": raw[offset : offset + 12]},
                            "finish_reason": None,
                        }
                    ],
                }
                yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                time.sleep(0.04)
            payload["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            yield "data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    with serve(upstream) as upstream_url:
        generator = OpenAICompatibleGenerator(
            model="synthetic-upstream",
            api_key="local-synthetic-probe",
            base_url=upstream_url + "/v1",
            verify_support=False,
        )
        # Host proxy variables may route even loopback traffic through a gateway.
        # The probe must only contact its own bound loopback sockets.
        from openai import OpenAI

        generator.client.close()
        generator.client = OpenAI(
            api_key="local-synthetic-probe",
            base_url=upstream_url + "/v1",
            http_client=httpx.Client(trust_env=False),
            max_retries=0,
        )
        service.generator = generator
        try:
            with (
                serve(main_module.app) as url,
                httpx.Client(base_url=url, timeout=10, trust_env=False) as client,
            ):
                started = time.monotonic()
                frames, name = [], None
                with client.stream(
                    "POST",
                    "/api/v1/chat/stream",
                    json={
                        "question": "SSE媒体类型",
                        "product": "probe",
                        "version": "1.0.0",
                    },
                ) as response:
                    assert response.status_code == 200, (
                        response.status_code,
                        response.read()[:500],
                    )
                    assert response.headers["content-type"].startswith("text/event-stream")
                    for line in response.iter_lines():
                        if line.startswith("event: "):
                            name = line[7:]
                        if line.startswith("data: "):
                            frames.append(
                                {
                                    "event": name,
                                    "data": json.loads(line[6:]),
                                    "received_ms": (time.monotonic() - started) * 1000,
                                }
                            )
                final = next(f for f in frames if f["event"] == "final")
                drafts = [f for f in frames if f["event"] == "draft"]
                assert drafts and drafts[0]["received_ms"] < final["received_ms"] - 100
                assert final["data"]["status"] == "answered"
                assert frames[-1]["event"] == "done"
                assert all(f["data"]["provisional"] for f in drafts)
                followup = client.post(
                    "/api/v1/chat",
                    json={
                        "question": "2.0.0",
                        "previous_turn": final["data"]["conversation"],
                    },
                )
                # Nonstream synthetic mode has no JSON completion endpoint; rewrite still
                # runs and filters first, independently of its deliberate model error.
                assert followup.json()["trace"]["scope"]["version"] == "2.0.0"
                assert all(
                    h["metadata"]["version"] == "2.0.0" for h in followup.json()["retrieval"]
                )
                assert client.get("/chat.js").status_code == 200
                report = {
                    "status": "passed",
                    "evidence_type": "real_loopback_synthetic_upstream",
                    "paid_api_calls": 0,
                    "model_quality_evaluated": False,
                    "first_draft_ms": drafts[0]["received_ms"],
                    "final_ms": final["received_ms"],
                    "events": frames,
                    "version_followup": followup.json(),
                    "server_cleanup": "context_managed",
                }
        finally:
            generator.client.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "first_draft_ms": report["first_draft_ms"],
                "final_ms": report["final_ms"],
            }
        )
    )


if __name__ == "__main__":
    main()
