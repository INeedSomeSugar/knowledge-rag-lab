from pathlib import Path

from app.chunking import TextChunker
from app.embeddings import HashingEmbedding
from app.generation import ExtractiveGenerator
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever
from app.service import RAGService
from app.storage import JsonChunkRepository


def build_service(directory: Path) -> RAGService:
    retriever = HybridRetriever(DenseRetriever(HashingEmbedding()), BM25Retriever())
    return RAGService(
        chunker=TextChunker(chunk_size=120, overlap=20),
        retriever=retriever,
        generator=ExtractiveGenerator(),
        repository=JsonChunkRepository(directory),
    )


def test_ingest_search_answer_and_persistence(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    result = service.ingest("制度.md", "正式员工每周最多远程办公两天。申请需提前一天审批。")

    hits = service.search("每周能远程办公几天", top_k=3)
    answer = service.answer("每周能远程办公几天", top_k=3)

    assert result["chunk_count"] == 1
    assert hits[0].chunk.source == "制度.md"
    assert answer["citations"][0]["source"] == "制度.md"
    assert build_service(tmp_path).list_documents()[0]["document_id"] == result["document_id"]


def test_delete_document(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    result = service.ingest("制度.md", "报销需要提供合规发票。")
    assert service.delete_document(str(result["document_id"])) is True
    assert service.list_documents() == []
