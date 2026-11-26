from __future__ import annotations

from src.agents.langgraph_rag import LangGraphRAGService
from src.infrastructure.llm.huggingface_embeddings import HuggingFaceEmbeddingProvider
from src.infrastructure.llm.local_embeddings import LocalHashEmbeddingProvider
from src.infrastructure.llm.local_llm import LocalExtractiveLLM
from src.infrastructure.llm.openai_embeddings import OpenAIEmbeddingProvider
from src.infrastructure.llm.openai_llm import OpenAILLM
from src.rag.embeddings import EmbeddingProvider
from src.rag.ingestion import (
    ChunkingStrategyRegistry, FixedWindowChunkingStrategy, PDFExtractor,
    PDFPlumberExtractor, RecursiveSemanticChunkingStrategy,
)
from src.rag.keyword import KeywordIndex
from src.rag.pipeline import RAGIngestionService, RAGRetrievalService
from src.rag.reranker import Reranker
from src.rag.vectorstore import VectorStore
from src.settings.rag_runtime import RagRuntimeSettings
from src.shared.interfaces.llm import LLM


def create_llm(settings: RagRuntimeSettings, client: type[OpenAILLM] = OpenAILLM) -> LLM:
    if settings.llm_provider == "local":
        return LocalExtractiveLLM()
    if settings.llm_provider == "qwen":
        if settings.dashscope_api_key is None:
            raise RuntimeError("Missing DASHSCOPE_API_KEY in environment.")
        return client(
            api_key=settings.dashscope_api_key.get_secret_value(),
            model=settings.dashscope_model_name,
            base_url=settings.dashscope_base_url,
            timeout_s=settings.dashscope_timeout_seconds,
            max_retries=settings.external_max_retries,
        )
    if not settings.openai_key:
        raise RuntimeError("Missing OPENAI_KEY in environment.")
    if not settings.model:
        raise RuntimeError("Missing MODEL in environment.")

    return client(
        api_key=settings.openai_key,
        model=settings.model,
        base_url=settings.ollama_base_url,
        timeout_s=settings.external_request_timeout_s,
        max_retries=settings.external_max_retries,
    )


def create_embedding_provider(settings: RagRuntimeSettings) -> EmbeddingProvider:
    if not settings.embedding_model:
        raise RuntimeError("Missing EMBEDDING_MODEL in environment.")
    provider_name = settings.embedding_provider

    if provider_name == "local":
        return LocalHashEmbeddingProvider()

    if provider_name == "openai":
        if not settings.openai_key:
            raise RuntimeError("Missing OPENAI_KEY in environment.")
        return OpenAIEmbeddingProvider(
            api_key=settings.openai_key,
            model=settings.embedding_model,
            base_url=settings.embedding_base_url,
            timeout_s=settings.external_request_timeout_s,
            max_retries=settings.external_max_retries,
        )

    if provider_name == "huggingface":
        return HuggingFaceEmbeddingProvider(model_name=settings.embedding_model)

    raise RuntimeError(
        f"Unsupported EMBEDDING_PROVIDER '{provider_name}'. Use 'local', 'openai' or 'huggingface'."
    )


def create_reranker(settings: RagRuntimeSettings) -> Reranker | None:
    if not settings.reranker_enabled:
        return None
    if not settings.reranker_model:
        raise RuntimeError("Missing RERANKER_MODEL in environment.")

    if settings.reranker_provider == "local":
        from src.infrastructure.reranker import LocalCrossEncoderReranker

        return LocalCrossEncoderReranker(
            model=settings.reranker_model, timeout_s=settings.reranker_timeout_s,
        )
    if not settings.reranker_api_key:
        raise RuntimeError("Missing RERANKER_API_KEY in environment.")

    from src.infrastructure.reranker import CohereReranker

    return CohereReranker(
        api_key=settings.reranker_api_key,
        model=settings.reranker_model,
        timeout_s=settings.reranker_timeout_s,
    )


def create_pdf_extractor(settings: RagRuntimeSettings) -> PDFExtractor:
    return PDFPlumberExtractor(
        dedupe_threshold=settings.rag_pdf_dedupe_threshold,
    )


def create_chunking_registry() -> ChunkingStrategyRegistry:
    return ChunkingStrategyRegistry(
        [
            FixedWindowChunkingStrategy(),
            RecursiveSemanticChunkingStrategy(),
        ]
    )


def build_rag_ingestion_service(
    settings: RagRuntimeSettings, *, embedding: EmbeddingProvider, vector_store: VectorStore,
    keyword_index: KeywordIndex | None, registry: ChunkingStrategyRegistry, pdf_extractor: PDFExtractor,
) -> RAGIngestionService:
    registry.resolve(settings.default_chunking_strategy)
    return RAGIngestionService(
        embedding_provider=embedding,
        vector_store=vector_store,
        chunking_registry=registry,
        default_chunking_strategy=settings.default_chunking_strategy,
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
        pdf_extractor=pdf_extractor,
        pdf_max_pages=settings.rag_pdf_max_pages,
        keyword_index=keyword_index,
    )


def build_rag_retrieval_service(
    settings: RagRuntimeSettings, *, embedding: EmbeddingProvider, vector_store: VectorStore,
    keyword_index: KeywordIndex | None, reranker: Reranker | None,
) -> RAGRetrievalService:
    return RAGRetrievalService(
        embedding_provider=embedding,
        vector_store=vector_store,
        default_top_k=settings.rag_top_k,
        prefetch_k=settings.rag_prefetch_k,
        reranker=reranker,
        keyword_index=keyword_index,
        dense_enabled=settings.dense_enabled,
        bm25_enabled=settings.bm25_enabled,
        dense_fetch_k=settings.dense_fetch_k,
        bm25_fetch_k=settings.bm25_fetch_k,
        rrf_k=settings.rrf_k,
        candidate_k=settings.hybrid_candidate_k,
        reranker_fail_open=settings.reranker_fail_open,
    )


def build_langgraph_rag_service(
    settings: RagRuntimeSettings, retrieval_service: RAGRetrievalService, llm: LLM,
) -> LangGraphRAGService:
    return LangGraphRAGService(
        retrieval_service=retrieval_service, llm=llm,
        max_retrieval_attempts=settings.agentic_max_retrieval_attempts,
    )
