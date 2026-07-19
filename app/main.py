from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import Settings
from app.factory import create_service
from app.loaders import load_bytes


settings = Settings()
service = create_service(settings)

app = FastAPI(
    title="Knowledge RAG Lab",
    version="0.1.0",
    description="文档摄取、混合检索、证据引用与检索评测 API",
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


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "embedding_provider": settings.embedding_provider,
        "llm_provider": settings.llm_provider,
        "document_count": len(service.list_documents()),
    }


@app.get("/api/v1/documents")
def list_documents() -> list[dict[str, object]]:
    return service.list_documents()


@app.post("/api/v1/documents/text", status_code=201)
def ingest_text(request: TextDocumentRequest) -> dict[str, object]:
    try:
        return service.ingest(request.source, request.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/documents/upload", status_code=201)
async def upload_document(file: Annotated[UploadFile, File()]) -> dict[str, object]:
    try:
        text = load_bytes(file.filename or "document.txt", await file.read())
        return service.ingest(file.filename or "未命名文档", text)
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
        "hits": [hit.to_dict() for hit in service.search(request.query, request.top_k)],
    }


@app.post("/api/v1/chat")
def chat(request: ChatRequest) -> dict[str, object]:
    return service.answer(request.question, request.top_k)
