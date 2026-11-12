"""Correlate HTTP requests with RAG traces without logging questions or credentials.

用 ``ContextVar`` 而不是把 request_id 一路当参数传：中间件、服务层、模型适配器
都需要它，但没有任何一层应该为了日志而改变函数签名。它在协程之间天然隔离，
``asyncio.to_thread`` 也会把当前上下文复制到工作线程，流式路径正依赖这一点。

刻意记录“发生了什么、耗时多少、错误类型是什么”，不记录问题原文、证据内容或密钥，
日志可以随请求编号交给外部排查而不泄露用户数据。
"""

from contextvars import ContextVar


request_id_context: ContextVar[str | None] = ContextVar("rag_request_id", default=None)
