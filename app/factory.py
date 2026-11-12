"""组装工厂：把配置翻译成一组具体实现。

这里是“配置名 → 类”的唯一映射点。两处安全约束值得注意：

1. 远程兼容端点必须 HTTPS，且 URL 不能内嵌凭据或查询参数；本地 Ollama 例外，
   因为它默认走 ``http://localhost``。
2. 每个分支失败都抛错，不做静默降级。例如请求 ``local_e5`` 但缺权重时，
   不会退回 Hashing——否则报告里的“语义检索”结论就会失效。

延迟导入（``local_e5`` / ``deepseek`` / ``bge``）是为了让零密钥路径不依赖 torch。
"""

from __future__ import annotations
import json
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
    """校验第三方兼容端点：必须带主机、scheme 合法、不含凭据与查询参数。

    明文 HTTP 只允许回环地址，避免把 API Key 和文档内容暴露在局域网或公网链路上。
    查询参数被禁止，是因为部分网关会把密钥放在 ``?api-key=`` 里，容易随日志泄露。
    """
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
    """按 ``EMBEDDING_PROVIDER`` 构造向量模型；未知取值直接报错。"""
    if settings.embedding_provider == "local_e5":
        # 延迟导入：不装 .[embedding] 时也能运行 Hashing 演示。
        from app.local_embeddings import LocalE5Embedding

        return LocalE5Embedding(
            model=settings.embedding_model,
            revision=settings.embedding_model_revision,
            directory=settings.embedding_model_dir,
            max_length=settings.embedding_max_length,
            batch_size=settings.embedding_batch_size,
            cpu_threads=settings.embedding_cpu_threads,
            device=settings.embedding_device,
        )
    if settings.embedding_provider == "hashing":
        return HashingEmbedding()
    if settings.embedding_provider in {"openai_compatible", "ollama"}:
        is_ollama = settings.embedding_provider == "ollama"
        if not is_ollama:
            require_compatible_endpoint(settings.embedding_base_url)
        return OpenAICompatibleEmbedding(
            model=settings.embedding_model,
            # Ollama 不校验 Key，但 OpenAI SDK 要求非空字段，因此填占位值。
            api_key="ollama" if is_ollama else settings.embedding_api_key,
            base_url=(
                settings.embedding_base_url or "http://localhost:11434/v1"
                if is_ollama
                else settings.embedding_base_url
            ),
        )
    raise ValueError(f"不支持的 EMBEDDING_PROVIDER：{settings.embedding_provider}")


def create_answer_generator(settings: Settings) -> AnswerGenerator:
    """按 ``LLM_PROVIDER`` 构造生成器；``extractive`` 是零密钥默认值。"""
    if settings.llm_provider == "extractive":
        return ExtractiveGenerator()
    if settings.llm_provider == "deepseek":
        # DeepSeek 有独立适配器：固定官方端点、强制 JSON 输出、校验模型目录。
        from app.deepseek import DeepSeekGenerator

        return DeepSeekGenerator.from_env(verify_support=settings.llm_verify_support)
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
            verify_support=settings.llm_verify_support,
        )
    raise ValueError(f"不支持的 LLM_PROVIDER：{settings.llm_provider}")


def create_cached_embedding(settings: Settings) -> CachedEmbedding:
    """给向量模型套一层磁盘缓存，缓存键必须覆盖所有影响向量的因素。

    命名空间包含 provider、模型名、端点、缓存修订号和模型自述（``descriptor``，
    含 revision、dtype、pooling、前缀等）。任何一项变化都会生成新命名空间，
    旧向量不会被误用；这是“换模型后指标变化可解释”的前提。
    """
    model = create_embedding_model(settings)
    identity = json.dumps(getattr(model, "descriptor", {}), sort_keys=True, ensure_ascii=False)
    return CachedEmbedding(
        model,
        settings.data_dir / "vectors.sqlite3",
        f"v2:{settings.embedding_provider}:{settings.embedding_model}:{settings.embedding_base_url}:{settings.embedding_cache_revision}:{identity}",
    )


def create_service(settings: Settings | None = None) -> RAGService:
    """组装完整服务；这是应用、脚本、MCP 与测试共用的唯一入口。"""
    settings = settings or Settings()
    settings.validate()
    embedding = create_cached_embedding(settings)
    generator = create_answer_generator(settings)

    dense = DenseRetriever(embedding)
    retriever = HybridRetriever(dense, BM25Retriever())
    reranker = None
    if settings.reranker_provider == "bge_cross_encoder":
        # 只在显式选择时加载，避免默认路径依赖 torch 与本地权重文件。
        from app.cross_encoder import BGEReranker

        reranker = BGEReranker(settings.reranker_model_dir, device=settings.reranker_device)
    return RAGService(
        chunker=TextChunker(
            settings.chunk_size, settings.chunk_overlap, strategy=settings.chunking_strategy
        ),
        retriever=retriever,
        generator=generator,
        repository=JsonChunkRepository(settings.data_dir),
        retrieval_strategy=settings.retrieval_strategy,
        context_char_budget=settings.context_char_budget,
        context_policy=settings.context_policy,
        reranker=reranker,
        reranker_candidate_k=settings.reranker_candidate_k,
    )
