"""Local, model-free question review; originals and dataset splits stay immutable."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Literal
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.chunking import TextChunker
from app.corpus import read_documents
from app.evaluation import EvaluationCase, load_evaluation_cases


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_digest(value: object) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


class ReviewConflict(ValueError):
    """An old page or changed input must not overwrite a review."""


class EvidenceEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: StrictStr = Field(min_length=1, max_length=500)
    quote: StrictStr = Field(min_length=1, max_length=50000)
    start_char: int | None = Field(default=None, ge=0, strict=True)


class ReviewEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    question: StrictStr = Field(min_length=1, max_length=10000)
    reference_answer: StrictStr = Field(max_length=30000)
    expected_status: Literal["answered", "insufficient_evidence", "needs_clarification"]
    evidence: list[EvidenceEdit] = Field(max_length=30)
    review_status: Literal["unreviewed", "human_verified"]
    reviewed_by: StrictStr = Field(default="", max_length=100)
    notes: StrictStr = Field(default="", max_length=10000)
    checks: dict[str, StrictBool] = Field(default_factory=dict)


CHECKS = {"question_and_scope", "answer_and_behavior", "evidence_or_absence"}


class QuestionReviewStore:
    def __init__(self, questions: Path, documents: Path, state_dir: Path, split: str):
        if split not in {"development", "test"}:
            raise ValueError("必须选择 development 或 test")
        self.questions, self.documents = questions.resolve(), documents.resolve()
        self.split = split
        self.lock = RLock()
        cases = load_evaluation_cases(self.questions)
        raw = [
            json.loads(line)
            for line in self.questions.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if any(not row.get("id") for row in raw):
            raise ValueError("复核工作台要求每题有稳定的 id")
        self.originals = {row["id"]: row for row in raw if row.get("split", "development") == split}
        if not self.originals:
            raise ValueError("所选划分没有问题")
        self.texts, corpus_hash = self._read_corpus()
        self.binding = {
            "questions_sha256": digest(self.questions.read_bytes()),
            "corpus_sha256": corpus_hash,
            "split": split,
            "case_ids": list(self.originals),
        }
        self.path = state_dir.resolve() / f"{self.binding['questions_sha256']}-{split}.json"
        protected = [self.questions, self.documents]
        if self.path == protected[0] or self.path.is_relative_to(protected[1]):
            raise ValueError("复核状态必须存放在原始问题及语料之外")
        for case in cases:
            if case.split == split:
                self._validate_row(self.originals[case.case_id])
        self.reviews: dict[str, dict] = {}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved.get("binding") != self.binding or saved.get("schema_version") != "1.0":
                raise ReviewConflict("复核记录与当前问题或语料不一致，请恢复原始输入或另开状态目录")
            for case_id, item in saved["reviews"].items():
                if case_id not in self.originals:
                    raise ReviewConflict("复核记录含未知问题")
                self._validate_row(item["row"])
                original = self.originals[case_id]
                for key in ("id", "split", "group_id", "category", "filters", "authorship"):
                    if item["row"].get(key) != original.get(key):
                        raise ReviewConflict("复核记录改动了冻结的身份、划分或版本范围")
                if type(item.get("revision")) is not int or item["revision"] < 1:
                    raise ReviewConflict("复核版本号不合法")
                self.reviews[case_id] = item

    def _read_corpus(self) -> tuple[dict, str]:
        texts = {
            source: {"text": TextChunker._normalize(text), "metadata": metadata}
            for source, text, metadata in read_documents(self.documents)
        }
        return texts, json_digest(texts)

    def _fresh(self) -> None:
        if digest(self.questions.read_bytes()) != self.binding["questions_sha256"]:
            raise ReviewConflict("原始问题文件已改变，当前复核会话已过期")
        _, corpus_hash = self._read_corpus()
        if corpus_hash != self.binding["corpus_sha256"]:
            raise ReviewConflict("语料内容或版本元数据已改变，当前复核会话已过期")
        if self.path.exists():
            disk = json.loads(self.path.read_text(encoding="utf-8"))
            if disk.get("binding") != self.binding or disk.get("reviews") != self.reviews:
                raise ReviewConflict("另一工作台已修改复核记录，请重启此工作台以加载最新记录")
        elif self.reviews:
            raise ReviewConflict("复核状态文件已被移除，请恢复后重试")

    def _validate_row(self, row: dict) -> None:
        case = EvaluationCase.from_dict(row, line_number=1)
        for source in case.relevant_sources:
            if source not in self.texts:
                raise ValueError("证据来源不在当前语料清单中")
            metadata = self.texts[source]["metadata"]
            if any(metadata.get(key) != value for key, value in case.filters.items()):
                raise ValueError("证据来源与问题的产品或版本范围不一致")
        for anchor in case.evidence:
            text = self.texts[anchor["source"]]["text"]
            if text[anchor["start_char"] : anchor["end_char"]] != anchor["quote"]:
                raise ValueError("证据摘录与原文范围不一致")
        if case.review_status == "human_verified":
            if set(row.get("review_checks", {})) != CHECKS or not all(
                row["review_checks"].values()
            ):
                raise ValueError("人工确认必须逐项完成三项核对")
            try:
                timestamp = datetime.fromisoformat(row["reviewed_at"])
                if timestamp.utcoffset() is None:
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("人工复核时间必须包含时区") from exc
            if not case.should_answer and not row.get("review_notes", "").strip():
                raise ValueError("不可回答或需澄清问题必须说明判定依据")

    def snapshot(self) -> dict:
        with self.lock:
            self._fresh()
            rows = []
            for case_id, row in self.originals.items():
                item = self.reviews.get(case_id, {})
                rows.append(
                    {"row": deepcopy(item.get("row", row)), "revision": item.get("revision", 0)}
                )
            return {
                "binding": deepcopy(self.binding),
                "cases": rows,
                "human_verified": sum(
                    item["row"].get("review_status") == "human_verified" for item in rows
                ),
                "total": len(rows),
                "model_calls": 0,
            }

    def document(self, case_id: str, source: str) -> dict:
        with self.lock:
            self._fresh()
            if case_id not in self.originals or source not in self.texts:
                raise ValueError("未知问题或来源")
            filters = self.originals[case_id].get("filters", {})
            document = self.texts[source]
            if any(document["metadata"].get(key) != value for key, value in filters.items()):
                raise ValueError("只能查看当前问题产品和版本范围内的语料")
            return {"source": source, **deepcopy(document)}

    def sources(self, case_id: str) -> list[str]:
        if case_id not in self.originals:
            raise ValueError("未知问题")
        filters = self.originals[case_id].get("filters", {})
        return [
            source
            for source, doc in self.texts.items()
            if all(doc["metadata"].get(key) == value for key, value in filters.items())
        ]

    def save(self, case_id: str, edit: ReviewEdit) -> dict:
        with self.lock:
            self._fresh()
            if case_id not in self.originals:
                raise ValueError("问题不属于当前复核划分")
            current = self.reviews.get(case_id, {})
            if edit.revision != current.get("revision", 0):
                raise ReviewConflict("此题已被另一页面修改，请刷新后重试")
            if set(edit.checks) - CHECKS:
                raise ValueError("未知复核检查项")
            row = deepcopy(self.originals[case_id])
            anchors = []
            for anchor in edit.evidence:
                if anchor.source not in self.sources(case_id):
                    raise ValueError("证据来源不属于当前产品和版本")
                text = self.texts[anchor.source]["text"]
                start = anchor.start_char
                if start is None or text[start : start + len(anchor.quote)] != anchor.quote:
                    if text.count(anchor.quote) != 1:
                        raise ValueError("摘录须在来源中逐字且唯一存在；可扩大摘录范围后重试")
                    start = text.index(anchor.quote)
                anchors.append(
                    {
                        "source": anchor.source,
                        "quote": anchor.quote,
                        "start_char": start,
                        "end_char": start + len(anchor.quote),
                    }
                )
            reviewed = edit.review_status == "human_verified"
            row.update(
                question=edit.question.strip(),
                reference_answer=edit.reference_answer.strip(),
                expected_status=edit.expected_status,
                should_answer=edit.expected_status == "answered",
                relevant_sources=list(dict.fromkeys(anchor["source"] for anchor in anchors)),
                evidence=anchors,
                review_status=edit.review_status,
                reviewed_by=edit.reviewed_by.strip() if reviewed else "",
                reviewed_at=datetime.now(timezone.utc).isoformat() if reviewed else "",
                review_notes=edit.notes.strip(),
                review_checks=edit.checks,
            )
            self._validate_row(row)
            updated = {"row": row, "revision": edit.revision + 1}
            reviews = {**self.reviews, case_id: updated}
            payload = {"schema_version": "1.0", "binding": self.binding, "reviews": reviews}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self.reviews = reviews
            return deepcopy(updated)

    def export(self) -> str:
        snapshot = self.snapshot()
        return "".join(
            json.dumps(item["row"], ensure_ascii=False) + "\n" for item in snapshot["cases"]
        )


def create_review_app(store: QuestionReviewStore) -> FastAPI:
    app = FastAPI(title="Knowledge RAG Lab · 问题人工复核", version="0.10.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def local_writes(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
                return JSONResponse(status_code=403, content={"detail": "不接受跨来源写入"})
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse(status_code=415, content={"detail": "写入必须使用 JSON"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ReviewConflict)
    async def conflict_handler(request: Request, exc: ReviewConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def validation_handler(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/", response_class=HTMLResponse)
    def home():
        return (Path(__file__).parent / "static" / "review.html").read_text(encoding="utf-8")

    @app.get("/api/session")
    def session():
        return store.snapshot()

    @app.get("/api/sources")
    def sources(case_id: str):
        return {"sources": store.sources(case_id)}

    @app.get("/api/document")
    def document(case_id: str, source: str):
        return store.document(case_id, source)

    @app.put("/api/cases/{case_id}")
    def save(case_id: str, edit: ReviewEdit):
        return store.save(case_id, edit)

    @app.get("/api/export")
    def export():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return Response(
            store.export(),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="questions.{store.split}.{timestamp}.jsonl"'
            },
        )

    return app
