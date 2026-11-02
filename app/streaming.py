"""Bounded SSE bridge for synchronous retrieval/model adapters."""

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import json
from threading import Event


class StreamDisconnected(BaseException):
    """Must pass through normal model-error handlers to prevent a second model call."""


def encode_sse(event: str, data: dict, sequence: int) -> str:
    return f"id: {sequence}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_answer(
    service, question, top_k, *, filters=None, previous_turn=None, heartbeat_seconds=10
):
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=16)
    stopped = Event()

    def emit(event, data):
        if stopped.is_set():
            raise StreamDisconnected()
        future = asyncio.run_coroutine_threadsafe(queue.put((event, data)), loop)
        while True:
            try:
                future.result(timeout=0.2)
                return
            except FutureTimeout:
                if stopped.is_set():
                    future.cancel()
                    raise StreamDisconnected()

    def worker():
        try:
            result = service.answer(
                question, top_k, filters=filters, previous_turn=previous_turn, on_event=emit
            )
            emit("final", result)
        except StreamDisconnected:
            return
        except Exception:
            emit("error", {"code": "stream_failed", "message": "流式请求失败，请查看服务日志。"})
        finally:
            if not stopped.is_set():
                try:
                    emit("done", {})
                except StreamDisconnected:
                    pass

    task = asyncio.create_task(asyncio.to_thread(worker))
    sequence = 0
    try:
        yield encode_sse("stage", {"stage": "started"}, sequence)
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), heartbeat_seconds)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            sequence += 1
            yield encode_sse(event, data, sequence)
            if event == "done":
                break
    finally:
        stopped.set()
        # Cancelling the awaiter does not kill a Python thread. Its next callback aborts
        # and closes the SDK stream; an in-flight read is bounded by the SDK timeout.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
