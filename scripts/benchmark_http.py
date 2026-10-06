"""Bounded loopback-only HTTP workload; tests demo behavior, not model quality."""

from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timezone
import argparse
import json
from math import ceil
import os
from pathlib import Path
import platform
from time import perf_counter
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from scripts.evaluate import code_fingerprint


WORKLOAD = (
    (
        "versioned_evidence",
        {
            "question": "前端携带 Cookie 跨域时怎样配置允许的源？",
            "product": "fastapi",
            "version": "0.115.0",
            "top_k": 5,
        },
        "evidence_only",
    ),
    (
        "version_clarification",
        {"question": "如何配置跨域？", "product": "fastapi", "top_k": 5},
        "needs_clarification",
    ),
    (
        "unknown_version",
        {"question": "如何配置跨域？", "product": "fastapi", "version": "uncollected", "top_k": 5},
        "insufficient_evidence",
    ),
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("HTTP 验证仅允许本机回环地址，不含凭据、路径、查询或片段")
    return base_url.rstrip("/")


def request_json(
    base_url: str, path: str, payload: dict | None = None, timeout: float = 10
) -> tuple[dict, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(base_url + path, data=data, headers={"Content-Type": "application/json"})
    # Never send a local test through an environment-configured HTTP proxy.
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
        return json.load(response), response.headers.get("X-Request-ID", "")


def inspect_demo(base_url: str, expected_instance: str | None = None) -> tuple[dict, dict]:
    health, _ = request_json(base_url, "/health", timeout=2)
    if (
        health.get("embedding_provider") != "hashing"
        or health.get("llm_provider") != "extractive"
        or health.get("retrieval_strategy") != "bm25"
    ):
        raise ValueError("HTTP 验证只接受 Hashing/BM25/抽取式演示，拒绝调用真实模型")
    if expected_instance is not None and health.get("instance_id") != expected_instance:
        raise ValueError("端口上的服务不是本次启动的进程")
    ready, _ = request_json(base_url, "/ready", timeout=2)
    catalog, _ = request_json(base_url, "/api/v1/catalog", timeout=2)
    if ready.get("status") != "ready" or len(catalog.get("products", {}).get("fastapi", [])) < 2:
        raise ValueError("固定 FastAPI 双版本语料尚未就绪")
    if "0.115.0" not in catalog["products"]["fastapi"]:
        raise ValueError("缺少固定验证版本 0.115.0")
    return health, ready


def run_workload(
    base_url: str,
    requests: int = 24,
    concurrency: int = 4,
    warmup: int = 3,
    expected_instance: str | None = None,
) -> dict:
    base_url = validate_base_url(base_url)
    if not 1 <= requests <= 1000 or not 1 <= concurrency <= 16 or not 0 <= warmup <= 30:
        raise ValueError("请求数范围 1..1000，并发 1..16，预热 0..30")
    health, ready = inspect_demo(base_url, expected_instance)

    def run_one(index: int) -> dict:
        name, payload, expected = WORKLOAD[index % len(WORKLOAD)]
        started = perf_counter()
        result = {"index": index, "workload": name, "ok": False, "expected_status": expected}
        try:
            body, request_id = request_json(base_url, "/api/v1/chat", payload)
            result.update(status=body.get("status"), request_id=request_id)
            if body.get("status") != expected:
                raise ValueError("unexpected application status")
            if not request_id or body.get("trace", {}).get("request_id") != request_id:
                raise ValueError("request correlation mismatch")
            if expected == "evidence_only" and (
                not body.get("citations")
                or not body.get("retrieval")
                or not body.get("context")
                or any(
                    hit.get("metadata", {}).get("version") != payload["version"]
                    for hit in body.get("context", [])
                )
            ):
                raise ValueError("missing or wrong-version evidence")
            if body.get("model_trace", {}).get("calls"):
                raise ValueError("unexpected model invocation")
            result["ok"] = True
        except Exception as exc:
            result["error_type"] = type(exc).__name__
        result["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
        return result

    for index in range(warmup):
        if not run_one(index)["ok"]:
            raise RuntimeError("预热行为检查失败，停止正式 HTTP 负载")
    started = perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(run_one, range(requests)))
    elapsed = perf_counter() - started
    times = sorted(row["elapsed_ms"] for row in results)
    successes = sum(row["ok"] for row in results)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "loopback demo HTTP workload; not model quality or production capacity",
        "configuration": {
            "base_url": base_url,
            "requests": requests,
            "concurrency": concurrency,
            "warmup_requests": warmup,
            "request_timeout_seconds": 10,
            "connections": "new connection per request",
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "logical_cpu_count": os.cpu_count(),
        },
        "server": health,
        "readiness": ready,
        "client_code_sha256": code_fingerprint(),
        "summary": {
            "completed_count": len(results),
            "success_count": successes,
            "failure_count": len(results) - successes,
            "elapsed_seconds": round(elapsed, 4),
            "completed_requests_per_second": round(len(results) / elapsed, 3),
            "latency_p50_ms": times[ceil(len(times) * 0.5) - 1],
            "latency_p95_ms": times[ceil(len(times) * 0.95) - 1],
            "latency_max_ms": times[-1],
            "status_counts": dict(Counter(row.get("status", "transport_error") for row in results)),
        },
        "notes": [
            "只有 3 类重复请求，不能当作同等数量的独立问题或效果评测集。",
            "延迟包含连接、响应正文与解析；不含客户端任务排队时间，分位数为 nearest-rank。",
            "客户端与服务端位于同机，共享资源；没有真实 Embedding/LLM，不推断线上容量。",
        ],
        "details": results,
    }


def write_report(report: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=24)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("报告已存在，请使用新输出路径")
    report = run_workload(args.base_url, args.requests, args.concurrency, args.warmup)
    write_report(report, args.output)
    print(json.dumps(report["summary"], ensure_ascii=False))
    if report["summary"]["failure_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
