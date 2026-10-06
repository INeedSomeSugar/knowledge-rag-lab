from __future__ import annotations

from typing import Annotated
from pathlib import Path
import json
import logging
import os
from time import perf_counter
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.factory import create_service
from app.loaders import load_bytes
from app.observability import request_id_context


settings = Settings()
service = create_service(settings)

app = FastAPI(
    title="Knowledge RAG Lab · 开发者技术支持",
    version="0.9.0",
    description="版本约束检索、证据引用核验与可复现评测",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_request(request: Request, call_next):
    request_id = uuid.uuid4().hex
    token = request_id_context.set(request_id)
    started = perf_counter()
    status_code = 500
    error_type = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as exc:
        error_type = type(exc).__name__
        return JSONResponse(
            status_code=500,
            content={
                "detail": "服务内部异常，请凭请求编号查看服务日志。",
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )
    finally:
        route = request.scope.get("route")
        logging.getLogger("rag.requests").info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "method": request.method,
                    "route": getattr(route, "path", "unmatched"),
                    "status_code": status_code,
                    "error_type": error_type,
                    "response_headers_ms": round((perf_counter() - started) * 1000, 3),
                }
            )
        )
        request_id_context.reset(token)


class TextDocumentRequest(BaseModel):
    source: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    product: str | None = Field(default=None, min_length=1, max_length=80)
    version: str | None = Field(default=None, min_length=1, max_length=80)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    product: str | None = Field(default=None, min_length=1, max_length=80)
    version: str | None = Field(default=None, min_length=1, max_length=80)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    product: str | None = Field(default=None, min_length=1, max_length=80)
    version: str | None = Field(default=None, min_length=1, max_length=80)


class VersionComparisonRequest(BaseModel):
    topic: str = Field(default="advanced/advanced-dependencies.md", max_length=150)
    before: str = Field(default="0.117.1", min_length=1, max_length=30)
    after: str = Field(default="0.118.0", min_length=1, max_length=30)
    section: str | None = Field(default=None, min_length=1, max_length=200)


def request_filters(request: SearchRequest | ChatRequest | TextDocumentRequest) -> dict[str, str]:
    return {key: value for key in ("product", "version") if (value := getattr(request, key))}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/versions", response_class=HTMLResponse, include_in_schema=False)
def version_lab() -> str:
    return (Path(__file__).parent / "static" / "versions.html").read_text(encoding="utf-8")


def load_version_catalog():
    from app.version_comparison import VersionEvidenceCatalog

    try:
        return VersionEvidenceCatalog()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="版本实验语料未安装，请运行下载脚本。") from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=503, detail="版本实验语料校验失败。") from exc


@app.get("/api/v1/versions/catalog")
def version_catalog() -> dict:
    return load_version_catalog().catalog()


@app.post("/api/v1/versions/compare")
def compare_versions(request: VersionComparisonRequest) -> dict:
    catalog = load_version_catalog()
    try:
        return catalog.compare(request.topic, request.before, request.after, request.section)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/versions/behavior")
def version_behavior() -> dict:
    from app.version_comparison import behavior_record

    return behavior_record()


@app.get("/api/v1/catalog")
def catalog() -> dict[str, object]:
    return {"products": service.catalog(), "index_revision": service.index_revision}


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "embedding_provider": settings.embedding_provider,
        "llm_provider": settings.llm_provider,
        "retrieval_strategy": settings.retrieval_strategy,
        "context_policy": service.context_policy,
        "document_count": len(service.list_documents()),
        "process_id": os.getpid(),
        "application_version": app.version,
        "instance_id": os.getenv("RAG_INSTANCE_ID", ""),
    }


@app.get("/ready")
def ready() -> JSONResponse:
    snapshot = service.retriever
    chunks = snapshot.dense.chunks
    return JSONResponse(
        status_code=200 if chunks else 503,
        content={
            "status": "ready" if chunks else "not_ready",
            "reason": "index_loaded" if chunks else "empty_index",
            "chunk_count": len(chunks),
            "index_revision": snapshot.revision,
            "model_connectivity": "not_checked",
        },
    )


@app.get("/api/v1/documents")
def list_documents() -> list[dict[str, object]]:
    return service.list_documents()


@app.post("/api/v1/documents/text", status_code=201)
def ingest_text(request: TextDocumentRequest) -> dict[str, object]:
    try:
        return service.ingest(request.source, request.text, request_filters(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/documents/upload", status_code=201)
async def upload_document(
    file: Annotated[UploadFile, File()],
    product: Annotated[str, Form()] = "",
    version: Annotated[str, Form()] = "",
) -> dict[str, object]:
    try:
        text = load_bytes(file.filename or "document.txt", await file.read())
        metadata = {
            key: value for key, value in (("product", product), ("version", version)) if value
        }
        return await run_in_threadpool(
            service.ingest, file.filename or "未命名文档", text, metadata
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/v1/documents/{document_id}", status_code=204)
def delete_document(document_id: str) -> None:
    if not service.delete_document(document_id):
        raise HTTPException(status_code=404, detail="文档不存在")


@app.post("/api/v1/retrieval/search")
def search(request: SearchRequest) -> dict[str, object]:
    return {
        "query": request.query,
        "hits": [
            hit.to_dict()
            for hit in service.search(
                request.query, request.top_k, filters=request_filters(request)
            )
        ],
    }


@app.post("/api/v1/chat")
def chat(request: ChatRequest) -> dict[str, object]:
    return service.answer(request.question, request.top_k, filters=request_filters(request))
