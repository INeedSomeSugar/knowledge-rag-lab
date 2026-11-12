"""Bounded SSE bridge for synchronous retrieval/model adapters.

桥接模型是“一个工作线程 + 一个有界队列”：

- 检索与模型调用是同步阻塞的，放进 ``to_thread`` 执行，事件循环只负责转发；
- 队列上限 16，客户端读得慢时生产者会被反压，不会无限堆积内存；
- 空闲超过 ``heartbeat_seconds`` 发送 SSE 注释行心跳，防止中间代理断开连接；
- 客户端断开时设置 ``stopped``，生产者的下一次 ``emit`` 立即抛
  :class:`StreamDisconnected`，从而中断模型流，避免“用户已走、token 还在烧”。

``StreamDisconnected`` 继承 ``BaseException`` 而非 ``Exception``，是为了穿透
生成器内部的 ``except Exception``，确保不会在断开后又发起一次模型调用。
"""

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import json
from threading import Event


class StreamDisconnected(BaseException):
    """Must pass through normal model-error handlers to prevent a second model call."""


def encode_sse(event: str, data: dict, sequence: int) -> str:
    """按 SSE 规范编码：``id`` 单调递增，便于客户端发现丢帧。"""
    return f"id: {sequence}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_answer(
    service, question, top_k, *, filters=None, previous_turn=None, heartbeat_seconds=10
):
    """把 ``RAGService.answer`` 的事件回调转成 SSE 流。

    顺序保证：``started`` 先于任何业务事件；``final`` 一定在 ``done`` 之前；
    出错时发送 ``error`` 后仍以 ``done`` 收尾，客户端无需区分“正常结束”和
    “异常结束”的读取逻辑。
    """
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=16)
    stopped = Event()

    def emit(event, data):
        """线程侧发送：把协程调度回事件循环，并等待入队完成。

        等待带 200ms 超时轮询，是为了在队列满时仍能及时感知客户端断开；
        若直接阻塞等待，断开的连接会让工作线程永久挂起。
        """
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
            # 客户端已断开，正常退出，不再尝试发送任何事件。
            return
        except Exception:
            # 细节只进日志，响应里给通用文案，避免把内部堆栈暴露给前端。
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
