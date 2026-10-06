"""Correlate HTTP requests with RAG traces without logging questions or credentials."""

from contextvars import ContextVar


request_id_context: ContextVar[str | None] = ContextVar("rag_request_id", default=None)
