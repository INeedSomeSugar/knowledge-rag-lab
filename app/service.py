from __future__ import annotations

import hashlib
from collections import defaultdict
from threading import RLock
from time import perf_counter
import uuid

from app.chunking import TextChunker
from app.domain import Chunk, SearchHit
from app.answers import GeneratedAnswer, REFUSAL
from app.context import expand_context
from app.generation import AnswerGenerator, ExtractiveGenerator
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever
from app.storage import JsonChunkRepository


class RAGService:
    def __init__(
        self,
        *,
        chunker: TextChunker,
        retriever: HybridRetriever,
        generator: AnswerGenerator,
        repository: JsonChunkRepository,
        retrieval_strategy: str = "hybrid",
    ) -> None:
        self.chunker = chunker
        self.retriever = retriever
        self.generator = generator
        self.repository = repository
        if retrieval_strategy not in {"bm25", "dense", "hybrid"}:
            raise ValueError("不支持的检索策略")
        self.retrieval_strategy = retrieval_strategy
        self._write_lock = RLock()
        self.index_revision = ""
        self._rebuild_index()

    def ingest(
        self, source: str, text: str, metadata: dict[str, object] | None = None
    ) -> dict[str, object]:
        return self.ingest_many([(source, text, metadata or {})])[0]

    def ingest_many(
        self, documents: list[tuple[str, str, dict[str, object]]]
    ) -> list[dict[str, object]]:
        with self._write_lock:
            current = self.repository.load()
            results = []
            for source, text, metadata in documents:
                clean_text = text.strip()
                source = source.strip().replace("\\", "/")
                if not clean_text or not source:
                    raise ValueError("文档来源及内容不能为空")
                product, version = metadata.get("product", ""), metadata.get("version", "")
                if bool(product) != bool(version):
                    raise ValueError("产品和版本必须同时提供")
                identity = f"{product}\0{version}\0{source}"
                document_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
                content_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
                chunks = self.chunker.split(document_id, source, clean_text)
                if not chunks:
                    raise ValueError("文档分块为空")
                for chunk in chunks:
                    chunk.metadata.update(metadata)
                    chunk.metadata["content_sha256"] = content_hash
                    chunk.id = hashlib.sha256(f"{chunk.id}:{content_hash}".encode()).hexdigest()[
                        :24
                    ]
                # Replace the same logical source within a version, including legacy IDs.
                current = [
                    chunk
                    for chunk in current
                    if not (
                        chunk.source == source
                        and chunk.metadata.get("product", "") == product
                        and chunk.metadata.get("version", "") == version
                    )
                ] + chunks
                results.append(
                    {
                        "document_id": document_id,
                        "source": source,
                        "chunk_count": len(chunks),
                        "version": version,
                    }
                )
            self._publish(current)
            return results

    def search(
        self, query: str, top_k: int, *, filters: dict[str, str] | None = None
    ) -> list[SearchHit]:
        if not query.strip():
            raise ValueError("查询内容不能为空")
        if not 1 <= top_k <= 100:
            raise ValueError("top_k 必须在 1 到 100 之间")
        return self._search(self.retriever, query.strip(), top_k, filters)

    def _search(
        self, snapshot: HybridRetriever, query: str, top_k: int, filters: dict[str, str] | None
    ) -> list[SearchHit]:
        retriever = {"hybrid": snapshot, "bm25": snapshot.sparse, "dense": snapshot.dense}[
            self.retrieval_strategy
        ]
        return retriever.search(query, top_k, filters=filters)

    def answer(
        self, question: str, top_k: int, *, filters: dict[str, str] | None = None
    ) -> dict[str, object]:
        if not question.strip() or not 1 <= top_k <= 100:
            raise ValueError("问题不能为空且 top_k 必须在 1 到 100 之间")
        started = perf_counter()
        # Capture one immutable index snapshot for this request, even during ingestion.
        snapshot = self.retriever
        scope = {key: value for key, value in (filters or {}).items() if value}
        trace: dict[str, object] = {
            "request_id": uuid.uuid4().hex,
            "index_revision": snapshot.revision,
            "retrieval_strategy": self.retrieval_strategy,
        }
        catalog = self.catalog(snapshot.dense.chunks)
        result: GeneratedAnswer | None = None
        product = scope.get("product")
        if catalog and not product:
            if len(catalog) == 1:
                scope["product"] = product = next(iter(catalog))
            else:
                result = GeneratedAnswer(
                    "needs_clarification", "请先选择要查询的产品。", reason="product_required"
                )
        if product and result is None:
            versions = catalog.get(product, [])
            if not versions:
                result = GeneratedAnswer(
                    "insufficient_evidence", "知识库中没有该产品的文档。", reason="unknown_product"
                )
            elif scope.get("version") and scope["version"] not in versions:
                result = GeneratedAnswer(
                    "insufficient_evidence",
                    "知识库未收录指定版本，请选择已有版本或导入对应文档。",
                    reason="unknown_version",
                )
            elif not scope.get("version"):
                if len(versions) > 1:
                    result = GeneratedAnswer(
                        "needs_clarification",
                        "请明确使用的版本：" + "、".join(versions) + "。",
                        reason="version_required",
                    )
                else:
                    scope["version"] = versions[0]
        hits: list[SearchHit] = []
        retrieved: list[SearchHit] = []
        trace["scope"] = scope
        trace["retrieval_ms"] = 0.0
        trace["generation_ms"] = 0.0
        if result is None:
            retrieval_start = perf_counter()
            try:
                retrieved = self._search(snapshot, question.strip(), top_k, scope)
                hits = expand_context(retrieved, snapshot.dense.chunks)
            except Exception as exc:
                result = GeneratedAnswer(
                    "error", "检索服务暂时不可用，请检查模型服务配置。", reason=type(exc).__name__
                )
            trace["retrieval_ms"] = round((perf_counter() - retrieval_start) * 1000, 2)
            if result is None:
                generation_start = perf_counter()
                try:
                    if isinstance(self.generator, ExtractiveGenerator) and not any(
                        hit.sparse_rank for hit in hits
                    ):
                        result = GeneratedAnswer(
                            "insufficient_evidence", REFUSAL, reason="no_lexical_evidence_in_demo"
                        )
                    else:
                        result = self.generator.generate(question, hits)
                except Exception as exc:
                    result = GeneratedAnswer(
                        "error",
                        "生成服务暂时不可用，请检查模型服务配置。",
                        reason=type(exc).__name__,
                    )
                trace["generation_ms"] = round((perf_counter() - generation_start) * 1000, 2)
        trace["total_ms"] = round((perf_counter() - started) * 1000, 2)
        trace["retrieved_count"] = len(retrieved)
        trace["context_count"] = len(hits)
        trace["context_chars"] = sum(len(hit.chunk.text) for hit in hits)
        assert result is not None
        return {
            "question": question,
            "answer": result.answer,
            "status": result.status,
            "reason": result.reason,
            "verification": result.verification,
            "claims": result.claims,
            "citations": [
                {**hits[number - 1].to_dict(), "citation_id": number}
                for number in result.citation_ids
            ],
            "retrieval": [hit.to_dict() for hit in retrieved],
            "context": [hit.to_dict() for hit in hits],
            "trace": trace,
            "available_versions": catalog,
        }

    def list_documents(self) -> list[dict[str, object]]:
        grouped: defaultdict[tuple[str, str], int] = defaultdict(int)
        for chunk in self.repository.load():
            grouped[(chunk.document_id, chunk.source)] += 1
        return [
            {"document_id": document_id, "source": source, "chunk_count": count}
            for (document_id, source), count in sorted(grouped.items(), key=lambda item: item[0][1])
        ]

    def delete_document(self, document_id: str) -> bool:
        with self._write_lock:
            current = self.repository.load()
            remaining = [chunk for chunk in current if chunk.document_id != document_id]
            if len(remaining) == len(current):
                return False
            self._publish(remaining)
            return True

    def catalog(self, chunks: list[Chunk] | None = None) -> dict[str, list[str]]:
        products: defaultdict[str, set[str]] = defaultdict(set)
        for chunk in self.retriever.dense.chunks if chunks is None else chunks:
            product, version = chunk.metadata.get("product"), chunk.metadata.get("version")
            if product and version:
                products[str(product)].add(str(version))
        return {product: sorted(versions) for product, versions in sorted(products.items())}

    @staticmethod
    def _revision(chunks: list[Chunk]) -> str:
        import json

        return hashlib.sha256(
            json.dumps(
                [chunk.to_dict() for chunk in chunks], sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()[:16]

    def _build_snapshot(self, chunks: list[Chunk]) -> HybridRetriever:
        old = self.retriever
        candidate = HybridRetriever(
            DenseRetriever(old.dense.embedding_model),
            BM25Retriever(old.sparse.k1, old.sparse.b),
            rrf_k=old.rrf_k,
            dense_weight=old.dense_weight,
            sparse_weight=old.sparse_weight,
        )
        candidate.index(chunks)
        candidate.revision = self._revision(chunks)
        return candidate

    def _publish(self, chunks: list[Chunk]) -> None:
        candidate = self._build_snapshot(chunks)
        # Persist only after successful embedding/indexing. Readers retain the old snapshot.
        self.repository.save(chunks)
        self.retriever = candidate
        self.index_revision = self._revision(chunks)

    def _rebuild_index(self) -> None:
        self.retriever = self._build_snapshot(self.repository.load())
        self.index_revision = self._revision(self.retriever.dense.chunks)
