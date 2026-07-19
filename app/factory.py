from __future__ import annotations

from app.chunking import TextChunker
from app.config import Settings
from app.embeddings import HashingEmbedding, OpenAICompatibleEmbedding
from app.generation import ExtractiveGenerator, OpenAICompatibleGenerator
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever
from app.service import RAGService
from app.storage import JsonChunkRepository


def create_service(settings: Settings | None = None) -> RAGService:
    settings = settings or Settings()
    settings.validate()

    if settings.embedding_provider == "hashing":
        embedding = HashingEmbedding()
    elif settings.embedding_provider in {"openai_compatible", "ollama"}:
        is_ollama = settings.embedding_provider == "ollama"
        embedding = OpenAICompatibleEmbedding(
            model=settings.embedding_model,
            api_key="ollama" if is_ollama else settings.embedding_api_key,
            base_url=(
                settings.embedding_base_url or "http://localhost:11434/v1"
                if is_ollama
                else settings.embedding_base_url
            ),
        )
    else:
        raise ValueError(f"不支持的 EMBEDDING_PROVIDER：{settings.embedding_provider}")

    if settings.llm_provider == "extractive":
        generator = ExtractiveGenerator()
    elif settings.llm_provider in {"openai_compatible", "ollama"}:
        is_ollama = settings.llm_provider == "ollama"
        generator = OpenAICompatibleGenerator(
            model=settings.llm_model,
            api_key="ollama" if is_ollama else settings.llm_api_key,
            base_url=(
                settings.llm_base_url or "http://localhost:11434/v1"
                if is_ollama
                else settings.llm_base_url
            ),
            enable_thinking=settings.llm_enable_thinking,
        )
    else:
        raise ValueError(f"不支持的 LLM_PROVIDER：{settings.llm_provider}")

    dense = DenseRetriever(embedding)
    retriever = HybridRetriever(dense, BM25Retriever())
    return RAGService(
        chunker=TextChunker(settings.chunk_size, settings.chunk_overlap),
        retriever=retriever,
        generator=generator,
        repository=JsonChunkRepository(settings.data_dir),
    )
