"""HTTP 入口：FastAPI 路由、请求校验与统一错误边界。

结构上只有三层：

1. **中间件**：生成 ``request_id``，把异常统一转成 500 JSON，并记录不含问题原文的
   访问日志；
2. **Pydantic 模型**：长度、范围与必填约束在进入业务前完成，业务层只做语义校验；
3. **路由**：尽可能薄，参数转换后直接调用 ``RAGService``，不在此处写检索逻辑。

``/health`` 与 ``/ready`` 的区分是部署语义：前者表示进程活着，后者表示索引已加载；
空索引返回 503，调用方据此不要把流量当成“可服务”。
"""

from __future__ import annotations

from typing import Annotated
from pathlib import Path
import json
import logging
import os
from time import perf_counter
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.factory import create_service
from app.loaders import load_bytes
from app.observability import request_id_context


# 模块导入即构建服务：索引在进程启动时加载一次，后续请求复用同一快照。
# 若配置非法，这里会直接失败（fail fast），而不是等到第一个请求。
settings = Settings()
service = create_service(settings)

app = FastAPI(
    title="Knowledge RAG Lab · AI 技术文档问答助手",
    version="0.12.0",
    description="版本约束检索、证据引用核验与可复现评测",
)
# 仅允许本地前端开发端口跨域；生产部署应改为明确的站点白名单。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_request(request: Request, call_next):
    """为每个请求建立可追踪的 request_id，并保证异常不会泄漏内部细节。"""
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
        # 对外只给请求编号，排查时用编号去日志里找堆栈，避免把路径/配置暴露给调用方。
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
    """导入纯文本文档；产品与版本要么都给，要么都不给（由服务层校验）。"""

    source: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    product: str | None = Field(default=None, min_length=1, max_length=80)
    version: str | None = Field(default=None, min_length=1, max_length=80)


class SearchRequest(BaseModel):
    """只检索请求；``top_k`` 上限 20 是接口约束，服务层另有 100 的硬上限。"""

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    product: str | None = Field(default=None, min_length=1, max_length=80)
    version: str | None = Field(default=None, min_length=1, max_length=80)


class PreviousTurn(BaseModel):
    """上一轮问题与作用域，仅用于规则改写；不包含上一轮回答文本。"""

    question: str = Field(min_length=1, max_length=4000)
    product: str | None = Field(default=None, min_length=1, max_length=80)
    version: str | None = Field(default=None, min_length=1, max_length=80)


class ChatRequest(BaseModel):
    """问答请求；``pattern=r"\\S"`` 确保纯空白字符串在进入服务前被拒绝。"""

    question: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    top_k: int = Field(default=5, ge=1, le=20)
    product: str | None = Field(default=None, min_length=1, max_length=80)
    version: str | None = Field(default=None, min_length=1, max_length=80)
    previous_turn: PreviousTurn | None = None


class VersionComparisonRequest(BaseModel):
    """版本对比请求：主题是语料内的相对路径，版本必须来自已冻结语料。"""

    topic: str = Field(default="advanced/advanced-dependencies.md", max_length=150)
    before: str = Field(default="0.117.1", min_length=1, max_length=30)
    after: str = Field(default="0.118.0", min_length=1, max_length=30)
    section: str | None = Field(default=None, min_length=1, max_length=200)


def request_filters(request: SearchRequest | ChatRequest | TextDocumentRequest) -> dict[str, str]:
    """只保留有值的 product/version，避免空字符串被当成过滤条件。"""
    return {key: value for key in ("product", "version") if (value := getattr(request, key))}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/chat.js", include_in_schema=False)
def chat_script():
    return FileResponse(Path(__file__).parent / "static" / "chat.js", media_type="text/javascript")


@app.get("/versions", response_class=HTMLResponse, include_in_schema=False)
def version_lab() -> str:
    return (Path(__file__).parent / "static" / "versions.html").read_text(encoding="utf-8")


def load_version_catalog():
    """按需加载版本对比语料；未安装或校验失败统一返回 503。

    延迟加载的原因：版本语料是独立资产（约百份文档），主问答不依赖它；
    如果它在启动时就失败，会把整个服务拖垮。
    """
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
    """进程存活与当前配置；不检查模型连通性，避免健康检查变成付费/慢请求。"""
    return {
        "status": "ok",
        "embedding_provider": settings.embedding_provider,
        "llm_provider": settings.llm_provider,
        "retrieval_strategy": settings.retrieval_strategy,
        "context_policy": service.context_policy,
        "reranker_provider": settings.reranker_provider,
        "conversation_rewrite": "one_turn_rules",
        "document_count": len(service.list_documents()),
        "process_id": os.getpid(),
        "application_version": app.version,
        "instance_id": os.getenv("RAG_INSTANCE_ID", ""),
    }


@app.get("/ready")
def ready() -> JSONResponse:
    """索引就绪检查：空索引返回 503，并显式声明模型连通性“未检查”。"""
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
    """上传并导入文档；解析与建索引都是阻塞操作，放线程池执行。"""
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
    return service.answer(
        request.question,
        request.top_k,
        filters=request_filters(request),
        previous_turn=request.previous_turn.model_dump(exclude_none=True)
        if request.previous_turn
        else None,
    )


@app.post("/api/v1/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """SSE 流式问答；``X-Accel-Buffering: no`` 用于关闭 Nginx 缓冲。"""
    from app.streaming import stream_answer

    return StreamingResponse(
        stream_answer(
            service,
            request.question,
            request.top_k,
            filters=request_filters(request),
            previous_turn=request.previous_turn.model_dump(exclude_none=True)
            if request.previous_turn
            else None,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
