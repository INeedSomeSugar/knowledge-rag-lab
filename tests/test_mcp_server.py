"""MCP 服务端：工具/资源契约、版本澄清与参数错误映射。

MCP 客户端看到的是另一套消费面，但语义必须与 HTTP 一致：同一索引、同一作用域校验、
同一状态码（``needs_clarification`` 等）。参数非法应转成工具错误，而不是让异常穿透
协议层导致连接中断。
"""

from __future__ import annotations

from collections.abc import Coroutine
import json
from pathlib import Path
from typing import Any, TypeVar

import pytest

mcp = pytest.importorskip("mcp")
from mcp import types  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from pydantic import AnyUrl  # noqa: E402

from app.chunking import TextChunker  # noqa: E402
from app.embeddings import HashingEmbedding  # noqa: E402
from app.generation import ExtractiveGenerator  # noqa: E402
from app.mcp_server import bootstrap_corpus, create_mcp_server  # noqa: E402
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever  # noqa: E402
from app.service import RAGService  # noqa: E402
from app.storage import JsonChunkRepository  # noqa: E402


T = TypeVar("T")


def _run_immediate(coro: Coroutine[Any, Any, T]) -> T:
    """Drive an in-memory MCP handler coroutine without opening OS loopback sockets."""
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value
    finally:
        coro.close()
    raise RuntimeError("Expected in-memory MCP coroutine to complete synchronously")


class InMemoryMCPSession:
    """In-memory client/server session dispatching MCP protocol requests to MCPServer."""

    def __init__(self, server: FastMCP) -> None:
        self._lowlevel = server._mcp_server

    def list_tools(self) -> types.ListToolsResult:
        handler = self._lowlevel.request_handlers[types.ListToolsRequest]
        res = _run_immediate(handler(types.ListToolsRequest()))
        assert isinstance(res.root, types.ListToolsResult)
        return res.root

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> types.CallToolResult:
        handler = self._lowlevel.request_handlers[types.CallToolRequest]
        req = types.CallToolRequest(
            params=types.CallToolRequestParams(name=name, arguments=arguments or {})
        )
        res = _run_immediate(handler(req))
        assert isinstance(res.root, types.CallToolResult)
        return res.root

    def list_resource_templates(self) -> types.ListResourceTemplatesResult:
        handler = self._lowlevel.request_handlers[types.ListResourceTemplatesRequest]
        res = _run_immediate(handler(types.ListResourceTemplatesRequest()))
        assert isinstance(res.root, types.ListResourceTemplatesResult)
        return res.root

    def read_resource(self, uri: str) -> types.ReadResourceResult:
        handler = self._lowlevel.request_handlers[types.ReadResourceRequest]
        req = types.ReadResourceRequest(
            params=types.ReadResourceRequestParams(uri=AnyUrl(uri))
        )
        res = _run_immediate(handler(req))
        assert isinstance(res.root, types.ReadResourceResult)
        return res.root


def build_hashing_service(tmp_path: Path) -> RAGService:
    retriever = HybridRetriever(DenseRetriever(HashingEmbedding()), BM25Retriever())
    return RAGService(
        chunker=TextChunker(chunk_size=300, overlap=40, strategy="sections"),
        retriever=retriever,
        generator=ExtractiveGenerator(),
        repository=JsonChunkRepository(tmp_path),
    )


def test_mcp_tools_and_resources_on_sample_data(tmp_path: Path) -> None:
    service = build_hashing_service(tmp_path)
    ingested = bootstrap_corpus(service, Path("sample_data"))
    assert len(ingested) == 3

    server = create_mcp_server(service)
    session = InMemoryMCPSession(server)

    tools_result = session.list_tools()
    tool_Map = {tool.name: tool for tool in tools_result.tools}
    assert {"list_catalog", "search_docs", "ask_docs"} <= set(tool_Map)
    for name in ("list_catalog", "search_docs", "ask_docs"):
        assert tool_Map[name].description
        assert tool_Map[name].inputSchema["type"] == "object"

    templates_result = session.list_resource_templates()
    template_uris = {tpl.uriTemplate for tpl in templates_result.resourceTemplates}
    assert "rag://chunk/{chunk_id}" in template_uris
    assert "rag://document/{document_id}" in template_uris

    catalog_call = session.call_tool("list_catalog", {})
    assert catalog_call.isError is False
    catalog_data = catalog_call.structuredContent
    assert isinstance(catalog_data, dict)
    assert catalog_data["document_count"] == 3
    assert catalog_data["chunk_count"] >= 9
    assert catalog_data["index_revision"] == service.index_revision

    search_call = session.call_tool(
        "search_docs",
        {"query": "P0 工单多久响应", "top_k": 3},
    )
    assert search_call.isError is False
    search_data = search_call.structuredContent
    assert isinstance(search_data, dict)
    assert search_data["hit_count"] >= 1
    top_hit = search_data["hits"][0]
    assert top_hit["source"] == "product_manual.md"
    assert top_hit["section"] == "工单优先级"
    assert top_hit["document_id"]
    assert top_hit["chunk_id"]
    assert top_hit["char_range"]["end_char"] > top_hit["char_range"]["start_char"]
    assert top_hit["score"] > 0
    assert "五分钟" in top_hit["text"]

    chunk_res = session.read_resource(top_hit["resource_uri"])
    chunk_payload = json.loads(chunk_res.contents[0].text)
    assert chunk_payload["id"] == top_hit["chunk_id"]
    assert chunk_payload["source"] == "product_manual.md"

    doc_res = session.read_resource(f"rag://document/{top_hit['document_id']}")
    doc_payload = json.loads(doc_res.contents[0].text)
    assert doc_payload["document_id"] == top_hit["document_id"]
    assert doc_payload["chunk_count"] >= 3

    ask_call = session.call_tool(
        "ask_docs",
        {"question": "正式员工每周最多远程办公几天", "top_k": 3},
    )
    assert ask_call.isError is False
    ask_data = ask_call.structuredContent
    assert isinstance(ask_data, dict)
    assert ask_data["status"] == "evidence_only"
    assert ask_data["verification"] == "extractive_only"
    assert "两天" in ask_data["answer"]
    assert ask_data["citations"][0]["source"] == "employee_handbook.md"
    assert ask_data["trace"]["retrieved_count"] >= 1
    assert ask_data["trace"]["index_revision"] == service.index_revision


def test_mcp_version_scope_clarification_and_filtering(tmp_path: Path) -> None:
    service = build_hashing_service(tmp_path)
    service.ingest(
        "fastapi/0.110.0/tutorial/cors.md",
        "# CORS\n\n## 配置\n\nFastAPI 0.110.0 使用 CORSMiddleware 配置 allow_origins。",
        {"product": "fastapi", "version": "0.110.0"},
    )
    service.ingest(
        "fastapi/0.115.0/tutorial/cors.md",
        "# CORS\n\n## 配置\n\nFastAPI 0.115.0 支持通配与显式源列表配置 CORSMiddleware。",
        {"product": "fastapi", "version": "0.115.0"},
    )

    session = InMemoryMCPSession(create_mcp_server(service))

    catalog = session.call_tool("list_catalog", {}).structuredContent
    assert catalog["products"] == {"fastapi": ["0.110.0", "0.115.0"]}

    clarify = session.call_tool(
        "ask_docs",
        {"question": "如何配置 CORSMiddleware", "product": "fastapi"},
    ).structuredContent
    assert clarify["status"] == "needs_clarification"
    assert clarify["reason"] == "version_required"
    assert "0.110.0" in clarify["answer"] and "0.115.0" in clarify["answer"]

    unknown_ver = session.call_tool(
        "ask_docs",
        {"question": "如何配置 CORSMiddleware", "product": "fastapi", "version": "0.999.0"},
    ).structuredContent
    assert unknown_ver["status"] == "insufficient_evidence"
    assert unknown_ver["reason"] == "unknown_version"

    scoped_search = session.call_tool(
        "search_docs",
        {
            "query": "CORSMiddleware",
            "product": "fastapi",
            "version": "0.115.0",
            "top_k": 5,
        },
    ).structuredContent
    assert scoped_search["hit_count"] >= 1
    assert {hit["version"] for hit in scoped_search["hits"]} == {"0.115.0"}

    scoped_ask = session.call_tool(
        "ask_docs",
        {
            "question": "如何配置 CORSMiddleware",
            "product": "fastapi",
            "version": "0.115.0",
            "top_k": 3,
        },
    ).structuredContent
    assert scoped_ask["status"] == "evidence_only"
    assert {c["version"] for c in scoped_ask["citations"]} == {"0.115.0"}
    assert scoped_ask["trace"]["scope"] == {"product": "fastapi", "version": "0.115.0"}


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("search_docs", {"query": "   ", "top_k": 3}),
        ("search_docs", {"query": "CORS", "top_k": 0}),
        ("search_docs", {"query": "CORS", "top_k": 25}),
        ("search_docs", {"query": "CORS", "product": "   "}),
        ("ask_docs", {"question": "   ", "top_k": 3}),
        ("ask_docs", {"question": "CORS", "version": "   "}),
    ],
)
def test_mcp_invalid_inputs_return_tool_error(
    tmp_path: Path, tool_name: str, args: dict[str, Any]
) -> None:
    service = build_hashing_service(tmp_path)
    service.ingest("guide.md", "CORS 配置说明。")
    session = InMemoryMCPSession(create_mcp_server(service))

    result = session.call_tool(tool_name, args)
    assert result.isError is True
    assert result.content and result.content[0].text
