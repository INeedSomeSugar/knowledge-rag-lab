"""End-to-end stdio verification of the Knowledge RAG Lab MCP server using the MCP Python SDK."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import AnyUrl


def _extract_structured(result: Any) -> dict[str, Any]:
    if getattr(result, "structuredContent", None) is not None:
        return dict(result.structuredContent)
    for block in getattr(result, "content", []):
        if getattr(block, "type", None) == "text":
            return json.loads(block.text)
    raise ValueError("No structured or JSON text content returned from MCP tool call")


async def run_verification(
    *,
    documents_dir: Path,
    data_dir: Path,
    output_path: Path,
    timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["EMBEDDING_PROVIDER"] = "hashing"
    env["LLM_PROVIDER"] = "extractive"
    env["RETRIEVAL_STRATEGY"] = "bm25"
    env["CHUNKING_STRATEGY"] = "sections"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "app.mcp_server",
            "--demo",
            "--bootstrap",
            "--documents",
            str(documents_dir),
            "--data-dir",
            str(data_dir),
        ],
        env=env,
    )

    with anyio.fail_after(timeout_seconds):
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                init_result = await session.initialize()

                tools_list = await session.list_tools()
                templates_list = await session.list_resource_templates()

                catalog_res = await session.call_tool("list_catalog", {})
                assert not catalog_res.isError, f"list_catalog failed: {catalog_res}"
                catalog = _extract_structured(catalog_res)

                search_res = await session.call_tool(
                    "search_docs",
                    {
                        "query": "CORSMiddleware allow_origins",
                        "product": "fastapi",
                        "version": "0.115.0",
                        "top_k": 3,
                    },
                )
                assert not search_res.isError, f"search_docs failed: {search_res}"
                search_payload = _extract_structured(search_res)

                clarify_res = await session.call_tool(
                    "ask_docs",
                    {
                        "question": "如何配置 CORSMiddleware 允许跨域？",
                        "product": "fastapi",
                    },
                )
                assert not clarify_res.isError, f"ask_docs clarify failed: {clarify_res}"
                clarify_payload = _extract_structured(clarify_res)

                ask_res = await session.call_tool(
                    "ask_docs",
                    {
                        "question": "如何配置 CORSMiddleware 允许跨域？",
                        "product": "fastapi",
                        "version": "0.115.0",
                        "top_k": 3,
                    },
                )
                assert not ask_res.isError, f"ask_docs answered failed: {ask_res}"
                ask_payload = _extract_structured(ask_res)

                top_chunk_uri = search_payload["hits"][0]["resource_uri"]
                resource_res = await session.read_resource(AnyUrl(top_chunk_uri))
                resource_chunk = json.loads(resource_res.contents[0].text)

    report: dict[str, Any] = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "mcp_sdk_version": importlib.metadata.version("mcp"),
        "transport": "stdio",
        "server_info": {
            "name": init_result.serverInfo.name,
            "version": init_result.serverInfo.version,
            "protocol_version": init_result.protocolVersion,
            "instructions": init_result.instructions,
        },
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "required_params": tool.inputSchema.get("required", []),
            }
            for tool in tools_list.tools
        ],
        "resource_templates": [
            {
                "name": tpl.name,
                "uri_template": tpl.uriTemplate,
                "mime_type": tpl.mimeType,
            }
            for tpl in templates_list.resourceTemplates
        ],
        "calls": {
            "list_catalog": {
                "product_versions": catalog["products"],
                "document_count": catalog["document_count"],
                "chunk_count": catalog["chunk_count"],
                "index_revision": catalog["index_revision"],
                "retrieval_strategy": catalog["retrieval_strategy"],
                "context_policy": catalog["context_policy"],
            },
            "search_docs": search_payload,
            "ask_docs_clarification": clarify_payload,
            "ask_docs_scoped": ask_payload,
            "read_resource_chunk": {
                "uri": top_chunk_uri,
                "chunk_id": resource_chunk["id"],
                "document_id": resource_chunk["document_id"],
                "source": resource_chunk["source"],
                "start_char": resource_chunk["start_char"],
                "end_char": resource_chunk["end_char"],
            },
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--documents",
        type=Path,
        default=Path("evaluation/support_corpus"),
        help="用于验证的语料目录",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("work/mcp-verify-index"),
        help="验证期间临时索引目录",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("work/mcp-verification.json"),
        help="验证结果 JSON 输出路径",
    )
    args = parser.parse_args()

    report = anyio.run(
        lambda: run_verification(
            documents_dir=args.documents,
            data_dir=args.data_dir,
            output_path=args.output,
        )
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(args.output),
                "mcp_sdk_version": report["mcp_sdk_version"],
                "tools": [t["name"] for t in report["tools"]],
                "document_count": report["calls"]["list_catalog"]["document_count"],
                "chunk_count": report["calls"]["list_catalog"]["chunk_count"],
                "search_hit_count": report["calls"]["search_docs"]["hit_count"],
                "clarify_status": report["calls"]["ask_docs_clarification"]["status"],
                "scoped_ask_status": report["calls"]["ask_docs_scoped"]["status"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
