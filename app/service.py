from __future__ import annotations

import hashlib
from collections import defaultdict

from app.chunking import TextChunker
from app.domain import SearchHit
from app.generation import AnswerGenerator
from app.retrieval import HybridRetriever
from app.storage import JsonChunkRepository


class RAGService:
    def __init__(
        self,
        *,
        chunker: TextChunker,
        retriever: HybridRetriever,
        generator: AnswerGenerator,
        repository: JsonChunkRepository,
    ) -> None:
        self.chunker = chunker
        self.retriever = retriever
        self.generator = generator
        self.repository = repository
        self._rebuild_index()

    def ingest(self, source: str, text: str) -> dict[str, object]:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("文档内容不能为空")
        document_id = hashlib.sha1(f"{source}\0{clean_text}".encode("utf-8")).hexdigest()[:16]
        chunks = self.chunker.split(document_id, source, clean_text)
        self.repository.replace_document(document_id, chunks)
        self._rebuild_index()
        return {"document_id": document_id, "source": source, "chunk_count": len(chunks)}

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        if not query.strip():
            raise ValueError("查询内容不能为空")
        return self.retriever.search(query.strip(), top_k)

    def answer(self, question: str, top_k: int) -> dict[str, object]:
        hits = self.search(question, top_k)
        answer = self.generator.generate(question, hits)
        return {
            "question": question,
            "answer": answer,
            "citations": [hit.to_dict() for hit in hits],
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
        deleted = self.repository.delete_document(document_id)
        if deleted:
            self._rebuild_index()
        return deleted

    def _rebuild_index(self) -> None:
        self.retriever.index(self.repository.load())
