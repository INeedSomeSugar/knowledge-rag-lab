"""服务编排：把分块、检索、上下文与生成串成一次可追踪的问答请求。

这个模块是系统的“唯一事实入口”，三条不变量集中在这里维护：

1. **写读分离**：写入用 ``_write_lock`` 串行化，发布时先构建新索引再原子替换引用；
   读请求在开始时抓取一次快照，构建期间继续读旧索引，不会看到半成品。
2. **作用域先行**：产品/版本在检索前就被校验或追问，避免“检错了再去纠正”。
   澄清与拒答都属于正常返回，不是异常。
3. **失败可归因**：检索异常与生成异常分别落到 ``error`` 状态并保留 ``reason``，
   记录阶段耗时与上下文用量，便于区分“检索没找到”和“模型没答好”。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from threading import RLock
from time import perf_counter
import uuid

from app.chunking import TextChunker
from app.domain import Chunk, SearchHit
from app.answers import GeneratedAnswer, REFUSAL
from app.context import CONTEXT_POLICIES, context_usage, expand_context
from app.generation import AnswerGenerator, ExtractiveGenerator
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever
from app.storage import JsonChunkRepository
from app.observability import request_id_context
from app.conversation import rewrite_turn


def answer_fields(result: GeneratedAnswer, hits: list[SearchHit]) -> dict[str, object]:
    """把生成结果与命中列表拼成对外的答案字段。

    ``citation_ids`` 是模型/解析器给出的编号（1 起），这里按编号回查 ``hits`` 并
    附上完整分块；编号越界会在 ``parse_grounded_answer`` 阶段就被拦下，因此这里
    可以直接索引。
    """
    return {
        "answer": result.answer,
        "status": result.status,
        "reason": result.reason,
        "verification": result.verification,
        "claims": result.claims,
        "model_trace": result.model_trace,
        "citations": [
            {**hits[number - 1].to_dict(), "citation_id": number} for number in result.citation_ids
        ],
    }


class RAGService:
    """一次问答所需的全部依赖与状态。

    依赖注入而非全局单例，目的是让评测脚本能用同一份代码换检索器、换生成器、
    换分块参数，并在测试里替换成确定性替身。
    """

    def __init__(
        self,
        *,
        chunker: TextChunker,
        retriever: HybridRetriever,
        generator: AnswerGenerator,
        repository: JsonChunkRepository,
        retrieval_strategy: str = "hybrid",
        context_char_budget: int = 16000,
        context_policy: str = "neighbors",
        reranker=None,
        reranker_candidate_k: int = 50,
    ) -> None:
        self.chunker = chunker
        self.retriever = retriever
        self.generator = generator
        self.repository = repository
        if retrieval_strategy not in {"bm25", "dense", "hybrid"}:
            raise ValueError("不支持的检索策略")
        self.retrieval_strategy = retrieval_strategy
        if context_char_budget < 1:
            raise ValueError("上下文字符预算必须大于 0")
        self.context_char_budget = context_char_budget
        if context_policy not in CONTEXT_POLICIES:
            raise ValueError("不支持的上下文策略")
        self.context_policy = context_policy
        # reranker 为 None 表示不重排；不为 None 时先扩大候选池再截断到 top_k。
        self.reranker = reranker
        self.reranker_candidate_k = reranker_candidate_k
        self._write_lock = RLock()
        self.index_revision = ""
        self._rebuild_index()

    def ingest(
        self, source: str, text: str, metadata: dict[str, object] | None = None
    ) -> dict[str, object]:
        """导入单篇文档；语义与 ``ingest_many`` 完全一致。"""
        return self.ingest_many([(source, text, metadata or {})])[0]

    def ingest_many(
        self, documents: list[tuple[str, str, dict[str, object]]]
    ) -> list[dict[str, object]]:
        """批量导入并按“产品 + 版本 + 来源”替换旧内容。

        ID 设计要点：

        - ``document_id`` 只由产品、版本、来源决定，同一逻辑文档重复导入保持不变；
        - ``content_sha256`` 绑定正文，正文变化会让分块 ID 变化，旧缓存自然失效；
        - 替换粒度是“同一版本内的同一来源”，因此不同版本的同名文件可以共存。

        整个过程持写锁，最后一次性发布；中途异常不会留下半份索引。
        """
        with self._write_lock:
            current = self.repository.load()
            results = []
            for source, text, metadata in documents:
                clean_text = text.strip()
                # 统一路径分隔符，避免 Windows 反斜杠与 POSIX 斜杠被当成两个来源。
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
                    # 二次哈希把内容指纹并入分块 ID：正文变了，缓存与引用都不会复用旧块。
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
        """只检索不生成，用于调参与前端“检索轨迹”展示。

        ``top_k`` 的 1..100 范围是接口层约束，调用方无需再做边界检查。
        """
        if not query.strip():
            raise ValueError("查询内容不能为空")
        if not 1 <= top_k <= 100:
            raise ValueError("top_k 必须在 1 到 100 之间")
        return self._search(self.retriever, query.strip(), top_k, filters)

    def _search(
        self, snapshot: HybridRetriever, query: str, top_k: int, filters: dict[str, str] | None
    ) -> list[SearchHit]:
        """在给定快照上执行单策略检索，并在配置了重排器时先扩池再截断。"""
        retriever = {"hybrid": snapshot, "bm25": snapshot.sparse, "dense": snapshot.dense}[
            self.retrieval_strategy
        ]
        candidates = retriever.search(
            query,
            max(top_k, self.reranker_candidate_k) if self.reranker else top_k,
            filters=filters,
        )
        if self.reranker:
            candidates, _ = self.reranker.rerank(query, candidates)
        return candidates[:top_k]

    def answer(
        self,
        question: str,
        top_k: int,
        *,
        filters: dict[str, str] | None = None,
        previous_turn: dict | None = None,
        on_event=None,
    ) -> dict[str, object]:
        """端到端回答：改写 → 作用域校验 → 检索 → 上下文 → 生成 → 追踪。

        返回结构同时服务三类消费者：前端展示（answer/citations）、评测脚本
        （status/claims/context/trace）与 MCP 工具（精简后的子集）。
        ``on_event`` 为流式回调，典型事件顺序为：rewrite → stage(retrieval) →
        [stage(reranking)] → retrieval → stage(generation) → draft* →
        [stage(verification)] → final（final/done 由 ``app.streaming`` 补齐）。
        """
        if not question.strip() or not 1 <= top_k <= 100:
            raise ValueError("问题不能为空且 top_k 必须在 1 到 100 之间")
        started = perf_counter()
        # Capture one immutable index snapshot for this request, even during ingestion.
        snapshot = self.retriever
        scope = {key: value for key, value in (filters or {}).items() if value}
        trace: dict[str, object] = {
            "request_id": request_id_context.get() or uuid.uuid4().hex,
            "index_revision": snapshot.revision,
            "retrieval_strategy": self.retrieval_strategy,
        }
        catalog = self.catalog(snapshot.dense.chunks)
        rewrite = rewrite_turn(question, scope, previous_turn, catalog)
        query, scope = rewrite["query"], rewrite["scope"]
        trace["query_rewrite"] = rewrite["trace"]
        if on_event:
            on_event("rewrite", rewrite)
        result: GeneratedAnswer | None = None
        product = scope.get("product")
        # 作用域门控：产品不明确 → 澄清；产品/版本不存在 → 拒答；
        # 产品存在但版本不唯一且未指定 → 追问版本。三条分支都在检索前完成。
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
                if on_event:
                    on_event("stage", {"stage": "retrieval"})
                retriever = {"hybrid": snapshot, "bm25": snapshot.sparse, "dense": snapshot.dense}[
                    self.retrieval_strategy
                ]
                candidates = retriever.search(
                    query,
                    max(top_k, self.reranker_candidate_k) if self.reranker else top_k,
                    filters=scope,
                )
                if self.reranker:
                    if on_event:
                        on_event("stage", {"stage": "reranking"})
                    candidates, trace["reranking"] = self.reranker.rerank(query, candidates)
                # retrieved 是检索结果（用于评测“检索是否命中”），
                # hits 是实际送入模型的上下文（含邻块），两者必须分开记录。
                retrieved = candidates[:top_k]
                hits = expand_context(
                    retrieved,
                    snapshot.dense.chunks,
                    char_budget=self.context_char_budget,
                    policy=self.context_policy,
                )
            except Exception as exc:
                result = GeneratedAnswer(
                    "error", "检索服务暂时不可用，请检查模型服务配置。", reason=type(exc).__name__
                )
            trace["retrieval_ms"] = round((perf_counter() - retrieval_start) * 1000, 2)
            if result is None:
                if on_event:
                    on_event("retrieval", {"hits": [h.to_dict() for h in retrieved]})
                    on_event("stage", {"stage": "generation"})
                generation_start = perf_counter()
                try:
                    # 抽取式回退只在演示模式使用；没有任何词面命中时直接拒答，
                    # 避免把“纯语义相似但无字面证据”的片段伪装成答案。
                    if isinstance(self.generator, ExtractiveGenerator) and not any(
                        hit.sparse_rank for hit in hits
                    ):
                        result = GeneratedAnswer(
                            "insufficient_evidence", REFUSAL, reason="no_lexical_evidence_in_demo"
                        )
                    else:
                        if on_event and hasattr(self.generator, "generate_stream"):
                            result = self.generator.generate_stream(query, hits, on_event)
                        else:
                            result = self.generator.generate(query, hits)
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
        trace.update(context_usage(hits))
        trace["context_char_budget"] = self.context_char_budget
        trace["context_policy"] = self.context_policy
        # 所有提前返回的分支（澄清/拒答）都必须有 result，这里断言兜底编码遗漏。
        assert result is not None
        return {
            "question": question,
            **answer_fields(result, hits),
            "retrieval": [hit.to_dict() for hit in retrieved],
            "context": [hit.to_dict() for hit in hits],
            "trace": trace,
            "available_versions": catalog,
            "conversation": {
                # 仅回复版本号时，对话上下文仍应显示上一轮的问题，避免前端把它当成新问题。
                "question": (
                    previous_turn["question"]
                    if previous_turn and rewrite["trace"].get("version_only")
                    else question
                ),
                **{k: scope[k] for k in ("product", "version") if k in scope},
            },
        }

    def list_documents(self) -> list[dict[str, object]]:
        """按文档聚合分块数；这是前端目录与 MCP ``list_catalog`` 的数据来源。"""
        grouped: defaultdict[tuple[str, str], int] = defaultdict(int)
        for chunk in self.repository.load():
            grouped[(chunk.document_id, chunk.source)] += 1
        return [
            {"document_id": document_id, "source": source, "chunk_count": count}
            for (document_id, source), count in sorted(grouped.items(), key=lambda item: item[0][1])
        ]

    def delete_document(self, document_id: str) -> bool:
        """删除整篇文档；文档不存在时返回 ``False`` 而不是抛错，由接口层转 404。"""
        with self._write_lock:
            current = self.repository.load()
            remaining = [chunk for chunk in current if chunk.document_id != document_id]
            if len(remaining) == len(current):
                return False
            self._publish(remaining)
            return True

    def catalog(self, chunks: list[Chunk] | None = None) -> dict[str, list[str]]:
        """列出“产品 → 已收录版本”，版本升序排列。

        没有产品/版本元数据的文档不会出现在目录里，也无法通过作用域校验被检索到；
        这是有意为之：版本感知问答不允许来源不明的证据混入。
        """
        products: defaultdict[str, set[str]] = defaultdict(set)
        for chunk in self.retriever.dense.chunks if chunks is None else chunks:
            product, version = chunk.metadata.get("product"), chunk.metadata.get("version")
            if product and version:
                products[str(product)].add(str(version))
        return {product: sorted(versions) for product, versions in sorted(products.items())}

    def _revision(self, chunks: list[Chunk]) -> str:
        """索引指纹：分块全量内容 + 向量模型命名空间。

        回答里的 ``index_revision`` 用于把一次结果绑定到确定的索引状态；改动分块、
        元数据或换向量模型都会改变指纹，因此旧报告不会被误认为当前实现的结果。
        """
        import json

        return hashlib.sha256(
            json.dumps(
                {
                    "chunks": [chunk.to_dict() for chunk in chunks],
                    "embedding": getattr(
                        self.retriever.dense.embedding_model, "namespace", "uncached"
                    ),
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()[:16]

    def _build_snapshot(self, chunks: list[Chunk]) -> HybridRetriever:
        """在不触碰当前索引的前提下构建新快照。

        复用旧检索器的超参数与向量模型，只替换数据；这样重建不会因为配置漂移
        导致“同一份数据、不同索引”的不可解释差异。
        """
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
        """发布新索引：先落盘再切换引用，读者永远看到完整快照。"""
        candidate = self._build_snapshot(chunks)
        # Persist only after successful embedding/indexing. Readers retain the old snapshot.
        self.repository.save(chunks)
        self.retriever = candidate
        self.index_revision = self._revision(chunks)

    def _rebuild_index(self) -> None:
        """启动时从磁盘恢复索引；空库也正常返回空检索器而不是报错。"""
        self.retriever = self._build_snapshot(self.repository.load())
        self.index_revision = self._revision(self.retriever.dense.chunks)
