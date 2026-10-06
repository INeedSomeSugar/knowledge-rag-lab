"""Fixed local ASGI reproduction cases; accepts no arbitrary code or user snippets."""

from __future__ import annotations

import asyncio
from importlib.metadata import version
import json
import platform
import sys

from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

CASE_IDS = ("stream_borrowed", "stream_owned", "background_borrowed", "background_owned")


async def run_case(case_id: str) -> dict:
    if case_id not in CASE_IDS:
        raise ValueError("Unknown fixed case")
    events = []
    messages = []

    class Resource:
        def __init__(self, owner):
            self.owner = owner
            self.closed = False
            events.append(f"{owner}:open")

        def read(self):
            events.append(f"{self.owner}:read:{'closed' if self.closed else 'open'}")
            if self.closed:
                raise RuntimeError("resource_closed")
            return "resource_available"

        def close(self):
            self.closed = True
            events.append(f"{self.owner}:close")

    async def dependency():
        resource = Resource("dependency")
        try:
            yield resource
        finally:
            resource.close()

    app = FastAPI()

    @app.get("/")
    async def route(tasks: BackgroundTasks, resource=Depends(dependency)):
        events.append("route:return")
        if case_id.startswith("stream"):
            async def generate():
                owned = case_id == "stream_owned"
                current = Resource("stream") if owned else resource
                try:
                    yield current.read().encode()
                finally:
                    if owned:
                        current.close()
            return StreamingResponse(generate())

        async def task():
            owned = case_id == "background_owned"
            current = Resource("task") if owned else resource
            try:
                current.read()
            finally:
                if owned:
                    current.close()
        tasks.add_task(task)
        return JSONResponse({"scheduled": True}, background=tasks)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.start":
            events.append(f"response:start:{message['status']}")
        elif message["type"] == "http.response.body":
            events.append("response:body:" + str(bool(message.get("more_body", False))))

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/", "raw_path": b"/", "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000), "root_path": "",
    }
    error = None
    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    body = b"".join(message.get("body", b"") for message in messages).decode("utf-8")
    return {
        "case_id": case_id, "execution_status": "failed" if error else "completed",
        "events": events, "error": error, "response_body": body,
        "response_status": next((m["status"] for m in messages if "status" in m), None),
        "response_completed": any(
            m["type"] == "http.response.body" and not m.get("more_body", False)
            for m in messages
        ),
    }


async def main():
    result = {
        "python": platform.python_version(), "fastapi": version("fastapi"),
        "starlette": version("starlette"), "pydantic": version("pydantic"),
        "cases": [await run_case(case_id) for case_id in CASE_IDS],
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise SystemExit("This runner accepts no arguments or external code")
    asyncio.run(main())
