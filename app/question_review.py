"""Local, model-free question review; originals and dataset splits stay immutable.

人工复核工作台的三条硬约束：

1. **原文件只读**：问题 JSONL 与语料都不写入，复核结果单独存到状态目录；
2. **绑定可验证**：状态文件记录问题文件与语料的 SHA256、划分与题目 ID 列表，
   任一输入变化都会让旧会话失效，避免“改完语料继续用旧标注”；
3. **并发安全**：每题带 revision，写入时比对；两个页面同时编辑，后提交者收到 409。

所有校验都在本地完成，不调用模型（``model_calls`` 恒为 0），因此复核结果可以
作为“人工核验”的独立证据，而不是模型自评。
"""

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
    """对结构化数据做稳定摘要：键排序 + 不转义中文，保证不同机器结果一致。"""
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


class ReviewConflict(ValueError):
    """An old page or changed input must not overwrite a review."""


class EvidenceEdit(BaseModel):
    """证据编辑项；``start_char`` 可省略，由服务端在原文中唯一定位。

    ``extra="forbid"`` 让拼错字段名直接失败，而不是被静默忽略后保存一条缺字段的记录。
    """

    model_config = ConfigDict(extra="forbid")
    source: StrictStr = Field(min_length=1, max_length=500)
    quote: StrictStr = Field(min_length=1, max_length=50000)
    start_char: int | None = Field(default=None, ge=0, strict=True)


class ReviewEdit(BaseModel):
    """一次保存请求的完整内容；必须带 ``revision`` 以支持乐观并发。"""

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


# 人工确认必须逐项勾选的三项；缺一项就不能标记 human_verified。
CHECKS = {"question_and_scope", "answer_and_behavior", "evidence_or_absence"}


class QuestionReviewStore:
    """复核状态存储：内存字典 + 单文件快照，写入原子替换。

    状态文件路径由 ``问题文件摘要 + 划分`` 决定，因此换一批问题不会覆盖旧复核记录；
    同时禁止把状态文件放进语料目录，防止被当成文档导入。
    """

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
        # 复核状态以 ID 为主键，没有稳定 ID 就无法安全地增量保存。
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
            # schema_version 参与校验：结构升级后旧状态必须显式迁移，不能猜字段。
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
        """读取语料并保存**规范化后**的文本。

        用的是分块器同一个 ``_normalize``，因此字符偏移与索引阶段完全一致；
        若这里改用原始文件字节，证据区间会整体错位。
        """
        texts = {
            source: {"text": TextChunker._normalize(text), "metadata": metadata}
            for source, text, metadata in read_documents(self.documents)
        }
        return texts, json_digest(texts)

    def _fresh(self) -> None:
        """每次读写前确认输入未变、磁盘状态未被他处修改。

        三类冲突都返回 ``ReviewConflict``（HTTP 409）：问题变了、语料变了、
        另一个工作台写过了。让用户刷新或重启，而不是用旧数据覆盖新数据。
        """
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
        """校验一行复核结果的内部一致性。

        重点检查三类问题：证据来源是否在语料清单内、来源元数据是否与问题的版本
        范围一致、引用区间是否与原文逐字相同。这些错误一旦进入状态文件，
        后续评测就会用错误的金标计算指标。
        """
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
                # 无时区的时间无法比较先后，直接拒绝，避免“本地时间当 UTC”的歧义。
                if timestamp.utcoffset() is None:
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("人工复核时间必须包含时区") from exc
            if not case.should_answer and not row.get("review_notes", "").strip():
                raise ValueError("不可回答或需澄清问题必须说明判定依据")

    def snapshot(self) -> dict:
        """返回前端所需的完整状态：原始行（或已保存版本）+ revision。"""
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
        """按题查看原文；只允许访问该题产品/版本范围内的来源。"""
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
        """列出该题可选来源；已按版本范围过滤，避免选到别的版本文档。"""
        if case_id not in self.originals:
            raise ValueError("未知问题")
        filters = self.originals[case_id].get("filters", {})
        return [
            source
            for source, doc in self.texts.items()
            if all(doc["metadata"].get(key) == value for key, value in filters.items())
        ]

    def save(self, case_id: str, edit: ReviewEdit) -> dict:
        """保存一道题的复核结果，成功返回新 revision。

        流程：校验 revision → 校验检查项 → 定位证据区间 → 覆盖可编辑字段 →
        整体校验 → 原子写盘 → 更新内存。任何一步失败都不写盘。
        """
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
                # 前端给的位置优先；不一致或缺失时要求摘录在原文中唯一出现，
                # 否则无法确定用户指的是哪一处，宁可报错也不猜。
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
                # should_answer 由 expected_status 派生，避免两个字段各写各的。
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
            # 临时文件带随机名：两个进程同时写也不会互相覆盖同一份 .tmp。
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
        """导出为 JSONL，保持原文件字段顺序无关（读取方按字段名解析）。"""
        snapshot = self.snapshot()
        return "".join(
            json.dumps(item["row"], ensure_ascii=False) + "\n" for item in snapshot["cases"]
        )


def create_review_app(store: QuestionReviewStore) -> FastAPI:
    """构建本地复核 Web 应用。

    安全边界按“只服务本机”设计：``TrustedHostMiddleware`` 限制主机名，
    写请求校验 Origin 与 JSON Content-Type，响应统一 ``no-store``。
    这不是面向公网的鉴权方案，也不打算是。
    """
    app = FastAPI(title="Knowledge RAG Lab · 问题人工复核", version="0.10.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def local_writes(request: Request, call_next):
        # 跨站表单可以伪造简单请求，因此写操作同时要求同源 Origin 与 JSON 类型。
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
        # 文件名带 UTC 时间戳，多次导出不会互相覆盖，也便于对照审核时间。
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return Response(
            store.export(),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="questions.{store.split}.{timestamp}.jsonl"'
            },
        )

    return app
