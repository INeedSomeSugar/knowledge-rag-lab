"""流式问答与单轮对话改写：事件顺序、草稿可撤回性与断开清理。

SSE 的风险在于“先给用户看了错的东西”：草稿必须是可撤回的临时态，引用校验失败时
要用终稿或错误覆盖；客户端断开后必须停止生产，避免继续消耗模型调用。
改写的边界也被固定：只有用户显式提供的版本能被继承。
"""

import asyncio
import json
from threading import Event
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient
import pytest

from app.conversation import rewrite_turn
from app.generation import OpenAICompatibleGenerator, partial_claim_text
import app.main as main
from app.streaming import StreamDisconnected, stream_answer
from tests.test_service import build_service


def events(response):
    return [
        (
            next(line[7:] for line in frame.splitlines() if line.startswith("event: ")),
            json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: "))),
        )
        for frame in response.text.split("\n\n")
        if "data: " in frame
    ]


def versioned_service(path):
    service = build_service(path)
    for version in ("0.110.0", "0.115.0"):
        service.ingest(
            "guide.md",
            "使用 yield 的依赖会释放资源。后台任务应自行管理资源。",
            {"product": "fastapi", "version": version},
        )
    return service


def test_stream_clarification_and_version_only_followup(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "service", versioned_service(tmp_path))
    client = TestClient(main.app)
    first = client.post(
        "/api/v1/chat/stream", json={"question": "yield依赖资源", "product": "fastapi"}
    )
    assert first.headers["content-type"].startswith("text/event-stream")
    frames = events(first)
    assert frames[0][0] == "stage" and frames[-1][0] == "done"
    initial = next(data for name, data in frames if name == "final")
    assert initial["status"] == "needs_clarification"
    second = client.post(
        "/api/v1/chat/stream",
        json={
            "question": "0.115.0",
            "previous_turn": initial["conversation"],
        },
    )
    final = next(data for name, data in events(second) if name == "final")
    assert final["status"] == "evidence_only"
    assert final["trace"]["query_rewrite"]["rewritten"] == "yield依赖资源"
    assert {h["metadata"]["version"] for h in final["retrieval"]} == {"0.115.0"}
    assert final["conversation"]["question"] == "yield依赖资源"


def test_rewrite_new_scope_and_unknown_version_do_not_silently_reuse_old():
    previous = {"question": "依赖资源如何释放", "product": "fastapi", "version": "0.110.0"}
    catalog = {"fastapi": ["0.110.0", "0.115.0"], "other": ["2.0"]}
    result = rewrite_turn("那后台任务呢？", {}, previous, catalog)
    assert result["scope"]["version"] == "0.110.0" and "依赖资源" in result["query"]
    result = rewrite_turn("0.999.0", {}, previous, catalog)
    assert result["scope"]["version"] == "0.999.0"
    result = rewrite_turn("其他问题", {"product": "other"}, previous, catalog)
    assert "version" not in result["scope"] and result["query"] == "其他问题"
    result = rewrite_turn("新问题", {"version": "0.115.0"}, previous, catalog)
    assert result["scope"]["version"] == "0.115.0"


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "  "},
        {"question": "x" * 4001},
        {"question": "x", "previous_turn": {"question": "x" * 4001}},
    ],
)
def test_stream_rejects_invalid_input_before_headers(payload):
    assert TestClient(main.app).post("/api/v1/chat/stream", json=payload).status_code == 422


def test_partial_json_draft_withholds_incomplete_escape():
    assert partial_claim_text('{"claims":[{"text":"a\\u4e') == "a"
    assert partial_claim_text('{"claims":[{"text":"a\\u4e2d\\n\\"b') == 'a中\n"b'


class FakeStream:
    def __init__(self, parts):
        self.parts, self.closed = parts, False

    def __enter__(self):
        return iter(self.parts)

    def __exit__(self, *args):
        self.closed = True


def part(text="", finish=None, usage=None):
    return NS(
        model="offline-test",
        usage=usage,
        choices=[NS(delta=NS(content=text), finish_reason=finish)],
    )


def test_real_stream_adapter_draft_is_replaced_when_citation_invalid(tmp_path):
    service = build_service(tmp_path)
    service.ingest("guide.md", "跨域需要配置允许源。")
    hits = service.search("跨域", 1)
    raw = '{"status":"answered","claims":[{"text":"草稿结论","evidence":[{"citation":1,"quote":"不存在的引文"}]}]}'
    stream = FakeStream([part(raw[:45]), part(raw[45:]), part(finish="stop")])
    generator = object.__new__(OpenAICompatibleGenerator)
    generator.model, generator.enable_thinking, generator.verify_support = "offline", None, False
    generator.client = NS(chat=NS(completions=NS(create=lambda **kwargs: stream)))
    emitted = []
    result = generator.generate_stream(
        "跨域", hits, lambda event, data: emitted.append((event, data))
    )
    assert emitted and emitted[-1][1]["provisional"] is True
    assert result.status != "answered" and "草稿结论" not in result.answer
    assert stream.closed


def test_stream_failure_clears_provisional_result(tmp_path, monkeypatch):
    service = build_service(tmp_path)
    service.ingest("guide.md", "跨域需要配置允许源。")

    class Broken:
        def generate_stream(self, question, hits, emit):
            emit("draft", {"text": "尚未核验", "provisional": True})
            raise RuntimeError("private upstream detail")

    service.generator = Broken()
    monkeypatch.setattr(main, "service", service)
    response = TestClient(main.app).post("/api/v1/chat/stream", json={"question": "跨域"})
    frames = events(response)
    final = next(data for name, data in frames if name == "final")
    assert final["status"] == "error" and "尚未核验" not in final["answer"]
    assert "private upstream" not in response.text


def test_disconnect_closes_sdk_before_verification(tmp_path):
    service = build_service(tmp_path)
    service.ingest("guide.md", "跨域需要配置允许源。")
    hits = service.search("跨域", 1)
    raw = '{"status":"answered","claims":[{"text":"草稿","evidence":[]}]}'
    stream = FakeStream([part(raw), part(finish="stop")])
    generator = object.__new__(OpenAICompatibleGenerator)
    generator.model, generator.enable_thinking, generator.verify_support = "offline", None, True
    generator.client = NS(chat=NS(completions=NS(create=lambda **kwargs: stream)))

    def cancel(event, data):
        raise StreamDisconnected()

    with pytest.raises(StreamDisconnected):
        generator.generate_stream("跨域", hits, cancel)
    assert stream.closed


def test_disconnect_releases_bounded_producer():
    released = Event()

    class Busy:
        def answer(self, *args, on_event, **kwargs):
            try:
                for _ in range(10000):
                    on_event("draft", {"text": "x"})
                return {"status": "answered"}
            finally:
                released.set()

    async def run():
        stream = stream_answer(Busy(), "q", 5)
        await anext(stream)
        await anext(stream)
        await stream.aclose()
        assert await asyncio.to_thread(released.wait, 3)

    asyncio.run(run())
