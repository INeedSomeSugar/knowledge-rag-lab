"""Exercise the real local-E5 HTTP service without LLM or external requests."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import time
import uuid

from app.local_embeddings import E5_DIRECTORY, E5_MODEL, E5_REVISION
from scripts.benchmark_http import request_json
from scripts.evaluate import code_fingerprint
from scripts.verify_local import child_runtime


def verify(output: Path) -> dict:
    if output.exists():
        raise ValueError("报告已存在，请使用新文件名")
    root = Path(__file__).resolve().parents[1]
    work = root / "work/semantic-http" / uuid.uuid4().hex
    work.mkdir(parents=True)
    executable, environment = child_runtime()
    environment.update(
        EMBEDDING_PROVIDER="local_e5",
        EMBEDDING_MODEL=E5_MODEL,
        EMBEDDING_MODEL_REVISION=E5_REVISION,
        EMBEDDING_MODEL_DIR=str(E5_DIRECTORY),
        EMBEDDING_DEVICE="cpu",
        EMBEDDING_MAX_LENGTH="512",
        EMBEDDING_BATCH_SIZE="8",
        EMBEDDING_CPU_THREADS="4",
        EMBEDDING_CACHE_REVISION="1",
        LLM_PROVIDER="extractive",
        CHUNKING_STRATEGY="sections",
        CHUNK_SIZE="500",
        CHUNK_OVERLAP="80",
        RETRIEVAL_STRATEGY="hybrid",
        CONTEXT_POLICY="neighbors",
        CONTEXT_CHAR_BUDGET="8000",
        DATA_DIR=str(work / "index"),
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    instance = environment["RAG_INSTANCE_ID"]
    rows = []

    def check(name, condition):
        rows.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)

    with (work / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [executable, "-X", "utf8", "-m", "scripts.serve", "--bootstrap", "--port", str(port)],
            cwd=root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 90
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"服务提前退出：{work / 'server.log'}")
                try:
                    health, _ = request_json(base_url, "/health", timeout=1)
                    if health.get("instance_id") == instance:
                        break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("本地语义服务启动超时")
                time.sleep(0.15)
            check(
                "configured_local_e5_hybrid_extractive",
                health.get("embedding_provider") == "local_e5"
                and health.get("retrieval_strategy") == "hybrid"
                and health.get("llm_provider") == "extractive",
            )
            ready, _ = request_json(base_url, "/ready")
            check(
                "nonempty_real_index",
                ready.get("chunk_count") == 735 and bool(ready.get("index_revision")),
            )
            body, _ = request_json(
                base_url,
                "/api/v1/retrieval/search",
                {
                    "query": "路径参数需要整数，传入foo为什么报错？",
                    "product": "fastapi",
                    "version": "0.115.0",
                    "top_k": 5,
                },
            )
            hits = body["hits"]
            check(
                "five_real_semantic_hybrid_hits",
                len(hits) == 5 and any(h["dense_rank"] for h in hits),
            )
            check("version_filter", all(h["metadata"].get("version") == "0.115.0" for h in hits))
            for name, payload, expected in (
                (
                    "missing_version",
                    {"question": "怎样配置跨域？", "product": "fastapi"},
                    "needs_clarification",
                ),
                (
                    "unknown_version",
                    {"question": "怎样配置跨域？", "product": "fastapi", "version": "absent"},
                    "insufficient_evidence",
                ),
                (
                    "evidence_answer",
                    {
                        "question": "路径参数需要整数，传入foo为什么报错？",
                        "product": "fastapi",
                        "version": "0.115.0",
                    },
                    "evidence_only",
                ),
            ):
                response, request_id = request_json(base_url, "/api/v1/chat", payload)
                check(
                    name,
                    response["status"] == expected
                    and response["trace"]["request_id"] == request_id,
                )
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    try:
        health, _ = request_json(base_url, "/health", timeout=1)
    except OSError:
        health = {}
    check(
        "owned_process_stopped",
        process.poll() is not None and health.get("instance_id") != instance,
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_sha256": code_fingerprint(),
        "model": E5_MODEL,
        "revision": E5_REVISION,
        "checks": rows,
        "passed_count": sum(r["passed"] for r in rows),
        "server_log": (work / "server.log").relative_to(root).as_posix(),
        "scope": "Real loopback HTTP, local E5 and extractive output; no LLM/production/answer-quality claim.",
        "model_api_calls": 0,
        "offline_model_loading": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
