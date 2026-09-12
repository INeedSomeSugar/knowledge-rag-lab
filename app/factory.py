from __future__ import annotations
from urllib.parse import urlparse

from app.chunking import TextChunker
from app.config import Settings
from app.embeddings import EmbeddingModel, HashingEmbedding, OpenAICompatibleEmbedding
from app.generation import AnswerGenerator, ExtractiveGenerator, OpenAICompatibleGenerator
from app.retrieval import BM25Retriever, DenseRetriever, HybridRetriever
from app.service import RAGService
from app.storage import JsonChunkRepository
from app.vector_cache import CachedEmbedding


def require_compatible_endpoint(base_url: str) -> None:
    parsed = urlparse(base_url)
    if (
        not parsed.hostname
        or parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.query
    ):
        raise ValueError("兼容模型接口必须显式配置有效的 Base URL，不能含凭据或查询参数")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程模型接口必须使用 HTTPS")


def create_embedding_model(settings: Settings) -> EmbeddingModel:
    if settings.embedding_provider == "hashing":
        return HashingEmbedding()
    if settings.embedding_provider in {"openai_compatible", "ollama"}:
        is_ollama = settings.embedding_provider == "ollama"
        if not is_ollama:
            require_compatible_endpoint(settings.embedding_base_url)
        return OpenAICompatibleEmbedding(
            model=settings.embedding_model,
            api_key="ollama" if is_ollama else settings.embedding_api_key,
            base_url=(
                settings.embedding_base_url or "http://localhost:11434/v1"
                if is_ollama
                else settings.embedding_base_url
            ),
        )
    raise ValueError(f"不支持的 EMBEDDING_PROVIDER：{settings.embedding_provider}")


def create_answer_generator(settings: Settings) -> AnswerGenerator:
    if settings.llm_provider == "extractive":
        return ExtractiveGenerator()
    if settings.llm_provider in {"openai_compatible", "ollama"}:
        is_ollama = settings.llm_provider == "ollama"
        if not is_ollama:
            require_compatible_endpoint(settings.llm_base_url)
        return OpenAICompatibleGenerator(
            model=settings.llm_model,
            api_key="ollama" if is_ollama else settings.llm_api_key,
            base_url=(
                settings.llm_base_url or "http://localhost:11434/v1"
                if is_ollama
                else settings.llm_base_url
            ),
            enable_thinking=settings.llm_enable_thinking,
        )
    raise ValueError(f"不支持的 LLM_PROVIDER：{settings.llm_provider}")


def create_cached_embedding(settings: Settings) -> CachedEmbedding:
    return CachedEmbedding(
        create_embedding_model(settings),
        settings.data_dir / "vectors.sqlite3",
        f"v1:{settings.embedding_provider}:{settings.embedding_model}:{settings.embedding_base_url}:{settings.embedding_cache_revision}",
    )


def create_service(settings: Settings | None = None) -> RAGService:
    settings = settings or Settings()
    settings.validate()
    embedding = create_cached_embedding(settings)
    generator = create_answer_generator(settings)

    dense = DenseRetriever(embedding)
    retriever = HybridRetriever(dense, BM25Retriever())
    return RAGService(
        chunker=TextChunker(
            settings.chunk_size, settings.chunk_overlap, strategy=settings.chunking_strategy
        ),
        retriever=retriever,
        generator=generator,
        repository=JsonChunkRepository(settings.data_dir),
        retrieval_strategy=settings.retrieval_strategy,
    )
