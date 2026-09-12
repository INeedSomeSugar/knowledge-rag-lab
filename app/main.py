from __future__ import annotations

from typing import Annotated
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.factory import create_service
from app.loaders import load_bytes


settings = Settings()
service = create_service(settings)

app = FastAPI(
    title="Knowledge RAG Lab · 开发者技术支持",
    version="0.4.0",
    description="版本约束检索、证据引用核验与可复现评测",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def request_filters(request: SearchRequest | ChatRequest | TextDocumentRequest) -> dict[str, str]:
    return {key: value for key in ("product", "version") if (value := getattr(request, key))}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


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
        "document_count": len(service.list_documents()),
    }


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
