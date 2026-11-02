"""Model Context Protocol (MCP) server exposing Knowledge RAG Lab tools and resources."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from app.config import Settings, _int_env, _optional_bool_env
from app.corpus import read_documents
from app.factory import create_service
from app.service import RAGService

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "未安装 MCP SDK，请先运行 `.venv\\Scripts\\python.exe -m pip install -e \".[mcp]\"`。"
    ) from exc


SERVER_NAME = "knowledge-rag-lab"
SERVER_INSTRUCTIONS = (
    "Knowledge RAG Lab 版本感知开发者技术支持知识库。"
    "建议先调用 list_catalog 查看已收录产品与版本，再调用 search_docs 检索原文分块或调用 ask_docs 获取带引用的回答。"
)


def settings_from_env() -> Settings:
    """Build Settings from current environment variables (respecting runtime overrides)."""
    return Settings(
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "hashing"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
        embedding_api_key=os.getenv("EMBEDDING_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")),
        embedding_base_url=os.getenv("EMBEDDING_BASE_URL", os.getenv("DASHSCOPE_BASE_URL", "")),
        llm_provider=os.getenv("LLM_PROVIDER", "extractive"),
        llm_model=os.getenv("LLM_MODEL", "Qwen/Qwen3-8B"),
        llm_enable_thinking=_optional_bool_env("LLM_ENABLE_THINKING"),
        llm_verify_support=_optional_bool_env("LLM_VERIFY_SUPPORT") is not False,
        llm_api_key=os.getenv("LLM_API_KEY", os.getenv("DASHSCOPE_API_KEY", "")),
        llm_base_url=os.getenv("LLM_BASE_URL", os.getenv("DASHSCOPE_BASE_URL", "")),
        chunk_size=_int_env("CHUNK_SIZE", 500),
        chunk_overlap=_int_env("CHUNK_OVERLAP", 80),
        chunking_strategy=os.getenv("CHUNKING_STRATEGY", "window"),
        embedding_cache_revision=os.getenv("EMBEDDING_CACHE_REVISION", "1"),
        embedding_model_revision=os.getenv(
            "EMBEDDING_MODEL_REVISION", "614241f622f53c4eeff9890bdc4f31cfecc418b3"
        ),
        embedding_model_dir=Path(
            os.getenv(
                "EMBEDDING_MODEL_DIR",
                "work/models/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3",
            )
        ),
        embedding_max_length=_int_env("EMBEDDING_MAX_LENGTH", 512),
        embedding_batch_size=_int_env("EMBEDDING_BATCH_SIZE", 8),
        embedding_cpu_threads=_int_env("EMBEDDING_CPU_THREADS", 4),
        embedding_device=os.getenv("EMBEDDING_DEVICE", "cpu"),
        retrieval_top_k=_int_env("RETRIEVAL_TOP_K", 5),
        retrieval_strategy=os.getenv("RETRIEVAL_STRATEGY", "hybrid"),
        reranker_provider=os.getenv("RERANKER_PROVIDER", "none"),
        reranker_model_dir=Path(
            os.getenv(
                "RERANKER_MODEL_DIR",
                "work/models/bge-reranker-base/2cfc18c9415c912f9d8155881c133215df768a70",
            )
        ),
        reranker_device=os.getenv("RERANKER_DEVICE", "cpu"),
        reranker_candidate_k=_int_env("RERANKER_CANDIDATE_K", 50),
        context_char_budget=_int_env("CONTEXT_CHAR_BUDGET", 16000),
        context_policy=os.getenv("CONTEXT_POLICY", "neighbors"),
        data_dir=Path(os.getenv("DATA_DIR", "data/index")),
    )


def bootstrap_corpus(service: RAGService, documents_dir: Path) -> list[dict[str, object]]:
    """Ingest a corpus directory into the service using the existing provenance-checked loader."""
    if not documents_dir.is_dir():
        raise ValueError(f"语料目录不存在：{documents_dir}")
    docs = list(read_documents(documents_dir))
    if not docs:
        raise ValueError(f"语料目录为空：{documents_dir}")
    result = service.ingest_many(docs)
    logging.getLogger("rag.mcp").info(
        "Bootstrapped %d documents from %s (index_revision=%s)",
        len(result),
        documents_dir,
        service.index_revision,
    )
    return result


def _clean_filters(product: str | None, version: str | None) -> dict[str, str]:
    filters: dict[str, str] = {}
    if product is not None:
        cleaned_product = product.strip()
        if not cleaned_product:
            raise ToolError("product 参数不能为空白字符串；若不限定产品请省略该参数。")
        if len(cleaned_product) > 80:
            raise ToolError("product 参数长度不能超过 80 个字符。")
        filters["product"] = cleaned_product
    if version is not None:
        cleaned_version = version.strip()
        if not cleaned_version:
            raise ToolError("version 参数不能为空白字符串；若不限定版本请省略该参数。")
        if len(cleaned_version) > 80:
            raise ToolError("version 参数长度不能超过 80 个字符。")
        filters["version"] = cleaned_version
    return filters


def create_mcp_server(
    service: RAGService | None = None,
    *,
    settings: Settings | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    """Create a FastMCP server bound to an existing or newly constructed RAGService."""
    resolved_settings = settings or settings_from_env()
    rag_service = service or create_service(resolved_settings)

    mcp = FastMCP(
        name=SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        host=host,
        port=port,
        log_level="WARNING",
    )

    @mcp.tool(
        name="list_catalog",
        description=(
            "列出当前知识库已收录的产品与对应版本目录、文档列表、分块总数及索引修订号。"
            "在调用 search_docs 或 ask_docs 之前可先调用此工具确认可用的 product 与 version。"
        ),
    )
    def list_catalog() -> dict[str, Any]:
        documents = rag_service.list_documents()
        chunks = rag_service.retriever.dense.chunks
        return {
            "products": rag_service.catalog(chunks),
            "document_count": len(documents),
            "chunk_count": len(chunks),
            "index_revision": rag_service.index_revision,
            "retrieval_strategy": rag_service.retrieval_strategy,
            "context_policy": rag_service.context_policy,
            "documents": documents,
        }

    @mcp.tool(
        name="search_docs",
        description=(
            "在知识库中执行版本感知检索（仅检索原文分块，不调用生成模型）。"
            "返回按相关性排序的分块列表，包含 document_id、chunk_id、source、section、product、"
            "version、字符范围（start_char/end_char）、检索分数与排名，以及原文片段。"
        ),
    )
    def search_docs(
        query: Annotated[
            str,
            Field(
                description="检索查询词或技术问题描述（非空字符串）",
                min_length=1,
                max_length=4000,
            ),
        ],
        product: Annotated[
            str | None,
            Field(
                default=None,
                description="可选产品名称过滤，例如 'fastapi'",
                max_length=80,
            ),
        ] = None,
        version: Annotated[
            str | None,
            Field(
                default=None,
                description="可选版本号过滤，例如 '0.115.0'",
                max_length=80,
            ),
        ] = None,
        top_k: Annotated[
            int,
            Field(
                default=5,
                description="返回的最大分块数量（1 到 20 之间）",
                ge=1,
                le=20,
            ),
        ] = 5,
    ) -> dict[str, Any]:
        cleaned_query = query.strip()
        if not cleaned_query:
            raise ToolError("查询内容不能为空（query 必须包含非空白字符）。")
        if len(cleaned_query) > 4000:
            raise ToolError("查询内容长度不能超过 4000 个字符。")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
            raise ToolError("top_k 必须是 1 到 20 之间的整数。")

        filters = _clean_filters(product, version)
        try:
            hits = rag_service.search(
                cleaned_query,
                top_k=top_k,
                filters=filters or None,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"检索执行失败：{type(exc).__name__}: {exc}") from exc

        formatted_hits: list[dict[str, Any]] = []
        for rank, hit in enumerate(hits, start=1):
            meta = hit.chunk.metadata
            entry: dict[str, Any] = {
                "rank": rank,
                "chunk_id": hit.chunk.id,
                "document_id": hit.chunk.document_id,
                "source": hit.chunk.source,
                "title": meta.get("title"),
                "section": meta.get("section"),
                "product": meta.get("product"),
                "version": meta.get("version"),
                "char_range": {
                    "start_char": hit.chunk.start_char,
                    "end_char": hit.chunk.end_char,
                },
                "start_char": hit.chunk.start_char,
                "end_char": hit.chunk.end_char,
                "score": round(hit.score, 6),
                "dense_rank": hit.dense_rank,
                "sparse_rank": hit.sparse_rank,
                "text": hit.chunk.text,
                "resource_uri": f"rag://chunk/{hit.chunk.id}",
                "metadata": meta,
            }
            if hit.rerank_score is not None:
                entry["rerank_score"] = hit.rerank_score
                entry["rerank_rank"] = hit.rerank_rank
            formatted_hits.append(entry)

        return {
            "query": cleaned_query,
            "filters": filters,
            "top_k": top_k,
            "hit_count": len(formatted_hits),
            "index_revision": rag_service.index_revision,
            "retrieval_strategy": rag_service.retrieval_strategy,
            "hits": formatted_hits,
        }

    @mcp.tool(
        name="ask_docs",
        description=(
            "基于知识库回答技术支持问题，返回带引用的答案、状态（answered / evidence_only / "
            "needs_clarification / insufficient_evidence / error）、引用分块详情与精简执行追踪。"
            "若知识库包含同一产品的多个版本且未指定 version，将返回 needs_clarification 提示可选版本；"
            "零密钥配置下自动使用抽取式证据回退（evidence_only）。"
        ),
    )
    def ask_docs(
        question: Annotated[
            str,
            Field(
                description="要咨询的技术问题（非空字符串）",
                min_length=1,
                max_length=4000,
            ),
        ],
        product: Annotated[
            str | None,
            Field(
                default=None,
                description="可选产品名称，例如 'fastapi'",
                max_length=80,
            ),
        ] = None,
        version: Annotated[
            str | None,
            Field(
                default=None,
                description="可选版本号，例如 '0.115.0'；多版本知识库未传时会提示澄清",
                max_length=80,
            ),
        ] = None,
        top_k: Annotated[
            int,
            Field(
                default=5,
                description="检索种子分块数量（1 到 20 之间）",
                ge=1,
                le=20,
            ),
        ] = 5,
    ) -> dict[str, Any]:
        cleaned_question = question.strip()
        if not cleaned_question:
            raise ToolError("问题不能为空（question 必须包含非空白字符）。")
        if len(cleaned_question) > 4000:
            raise ToolError("问题长度不能超过 4000 个字符。")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
            raise ToolError("top_k 必须是 1 到 20 之间的整数。")

        filters = _clean_filters(product, version)
        try:
            raw = rag_service.answer(
                cleaned_question,
                top_k=top_k,
                filters=filters or None,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"问答执行失败：{type(exc).__name__}: {exc}") from exc

        citations: list[dict[str, Any]] = []
        for item in raw.get("citations", []):
            meta = item.get("metadata", {})
            citations.append(
                {
                    "citation_id": item.get("citation_id"),
                    "chunk_id": item.get("chunk_id"),
                    "document_id": item.get("document_id"),
                    "source": item.get("source"),
                    "title": meta.get("title"),
                    "section": meta.get("section"),
                    "product": meta.get("product"),
                    "version": meta.get("version"),
                    "char_range": {
                        "start_char": item.get("start_char"),
                        "end_char": item.get("end_char"),
                    },
                    "start_char": item.get("start_char"),
                    "end_char": item.get("end_char"),
                    "score": item.get("score"),
                    "text": item.get("text"),
                    "resource_uri": f"rag://chunk/{item.get('chunk_id')}",
                }
            )

        raw_trace = raw.get("trace", {})
        short_trace = {
            "request_id": raw_trace.get("request_id"),
            "index_revision": raw_trace.get("index_revision"),
            "retrieval_strategy": raw_trace.get("retrieval_strategy"),
            "context_policy": raw_trace.get("context_policy"),
            "scope": raw_trace.get("scope", {}),
            "retrieved_count": raw_trace.get("retrieved_count", 0),
            "context_count": raw_trace.get("context_count", 0),
            "context_chars": raw_trace.get("context_chars", 0),
            "retrieval_ms": raw_trace.get("retrieval_ms", 0.0),
            "generation_ms": raw_trace.get("generation_ms", 0.0),
            "total_ms": raw_trace.get("total_ms", 0.0),
        }

        return {
            "question": raw.get("question", cleaned_question),
            "status": raw.get("status"),
            "reason": raw.get("reason", ""),
            "verification": raw.get("verification", "not_applicable"),
            "answer": raw.get("answer", ""),
            "claims": raw.get("claims", []),
            "citations": citations,
            "available_versions": raw.get("available_versions", {}),
            "trace": short_trace,
        }

    @mcp.resource(
        "rag://chunk/{chunk_id}",
        name="chunk_by_id",
        description="按 chunk_id 读取单个已索引分块的原文、字符范围与元数据（JSON）",
        mime_type="application/json",
    )
    def read_chunk(chunk_id: str) -> str:
        target = chunk_id.strip()
        for chunk in rag_service.retriever.dense.chunks:
            if chunk.id == target:
                return json.dumps(chunk.to_dict(), ensure_ascii=False, indent=2)
        raise ValueError(f"未找到分块：{target}")

    @mcp.resource(
        "rag://document/{document_id}",
        name="document_by_id",
        description="按 document_id 读取整篇文档的所有分块及元数据（JSON）",
        mime_type="application/json",
    )
    def read_document(document_id: str) -> str:
        target = document_id.strip()
        matched = [
            chunk
            for chunk in rag_service.retriever.dense.chunks
            if chunk.document_id == target
        ]
        if not matched:
            raise ValueError(f"未找到文档：{target}")
        matched.sort(key=lambda item: item.start_char)
        first_meta = matched[0].metadata
        payload = {
            "document_id": target,
            "source": matched[0].source,
            "product": first_meta.get("product"),
            "version": first_meta.get("version"),
            "chunk_count": len(matched),
            "chunks": [chunk.to_dict() for chunk in matched],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    return mcp


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="MCP 传输协议（默认 stdio，可选 streamable-http 或 sse）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 传输监听地址")
    parser.add_argument("--port", type=int, default=8000, help="HTTP 传输监听端口")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="启用零密钥演示配置（Hashing 向量 + 抽取式回答 + 默认自动导入语料）",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="启动前导入 --documents 指定目录下的文档",
    )
    parser.add_argument(
        "--documents",
        type=Path,
        default=Path("evaluation/support_corpus"),
        help="启动导入的语料目录（默认 evaluation/support_corpus，也可设为 sample_data）",
    )
    parser.add_argument("--data-dir", type=Path, help="覆盖 DATA_DIR 索引存储目录")
    parser.add_argument(
        "--context-policy",
        choices=["neighbors", "merged_neighbors"],
        help="覆盖 CONTEXT_POLICY 上下文拼接策略",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1 到 65535 之间")

    # Never log to stdout in MCP mode; route all logs to stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="[%(name)s] %(levelname)s: %(message)s",
        force=True,
    )

    demo_enabled = args.demo or os.getenv("RAG_MCP_DEMO", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if demo_enabled:
        os.environ["EMBEDDING_PROVIDER"] = "hashing"
        os.environ["LLM_PROVIDER"] = "extractive"
        os.environ.setdefault("DATA_DIR", "data/demo-index")
        os.environ.setdefault("RETRIEVAL_STRATEGY", "bm25")
        os.environ.setdefault("CHUNKING_STRATEGY", "sections")
    if args.data_dir is not None:
        os.environ["DATA_DIR"] = str(args.data_dir)
    if args.context_policy is not None:
        os.environ["CONTEXT_POLICY"] = args.context_policy

    settings = settings_from_env()
    service = create_service(settings)

    should_bootstrap = args.bootstrap or os.getenv("RAG_MCP_BOOTSTRAP", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if should_bootstrap or (
        demo_enabled and not service.retriever.dense.chunks and args.documents.is_dir()
    ):
        bootstrap_corpus(service, args.documents)

    server = create_mcp_server(
        service,
        settings=settings,
        host=args.host,
        port=args.port,
    )
    transport: Literal["stdio", "sse", "streamable-http"] = args.transport
    server.run(transport=transport)


if __name__ == "__main__":
    main()
