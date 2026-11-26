from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents import AgentAskPipeline, AgentService, QueryRefinementService
from src.agents.langgraph_rag import LangGraphRAGService
from src.infrastructure.database import get_db
from src.infrastructure.llm.openai_llm import OpenAILLM
from src.infrastructure.database import AsyncSessionFactory
from src.modules.evaluation.judge import ContextRelevanceJudge
from src.modules.evaluation.retriever import (
    EvaluationRetriever,
    RAGRetrievalEvaluatorAdapter,
)
from src.modules.evaluation.service import RetrievalEvaluationService
from src.rag import runtime as rag_runtime
from src.rag.embeddings import EmbeddingProvider
from src.rag.ingestion import (
    ChunkingStrategyRegistry,
    PDFExtractor,
)
from src.rag.pipeline import RAGIngestionService, RAGRetrievalService
from src.rag.reranker import Reranker
from src.rag.vectorstore import VectorStore
from src.settings.config import settings
from src.shared.interfaces.llm import LLM
from src.tools import PingTool, RetrieverTool, ToolRegistry
from src.modules.documents.dependencies import DocumentsRepositoryDep
from src.modules.access.dependencies import PermissionServiceDep
from src.rag.keyword import KeywordIndex
from src.infrastructure.keyword import SQLiteBM25Index

if TYPE_CHECKING:
    from src.modules.semantic_cache.repository import SemanticCacheRepository
    from src.modules.semantic_cache.service import SemanticCacheService

DbSessionDep = Annotated[AsyncSession, Depends(get_db)]


@lru_cache
def get_llm() -> LLM:
    return rag_runtime.create_llm(settings, OpenAILLM)


LLMDep = Annotated[LLM, Depends(get_llm)]


def get_query_refinement_service(llm: LLMDep) -> QueryRefinementService:
    return QueryRefinementService(
        llm=llm,
        enabled=settings.query_refinement_enabled,
        temperature=settings.query_refinement_temperature,
        max_tokens=settings.query_refinement_max_tokens,
    )


QueryRefinementServiceDep = Annotated[
    QueryRefinementService, Depends(get_query_refinement_service)
]


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    return rag_runtime.create_embedding_provider(settings)


EmbeddingProviderDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]


def get_semantic_cache_repository(session: DbSessionDep) -> "SemanticCacheRepository":
    from src.modules.semantic_cache.repository import SemanticCacheRepository

    return SemanticCacheRepository(session)


SemanticCacheRepositoryDep = Annotated[
    "SemanticCacheRepository", Depends(get_semantic_cache_repository)
]


def get_semantic_cache_service(
    repository: SemanticCacheRepositoryDep,
) -> "SemanticCacheService":
    from src.modules.semantic_cache.service import SemanticCacheService

    return SemanticCacheService(
        repository=repository,
        enabled=settings.semantic_cache_enabled,
        similarity_threshold=settings.semantic_cache_similarity_threshold,
    )


SemanticCacheServiceDep = Annotated["SemanticCacheService", Depends(get_semantic_cache_service)]


@lru_cache
def get_vector_store() -> VectorStore:
    from src.infrastructure.vector_db.chroma_vectorstore import ChromaVectorStore

    return ChromaVectorStore(
        persist_dir=settings.chroma_persist_dir,
        collection_name=settings.resolved_rag_collection_name(),
    )


VectorStoreDep = Annotated[VectorStore, Depends(get_vector_store)]


@lru_cache
def get_keyword_index() -> KeywordIndex:
    return SQLiteBM25Index(database_path=settings.bm25_database_path)


KeywordIndexDep = Annotated[KeywordIndex, Depends(get_keyword_index)]


@lru_cache
def get_reranker() -> Reranker | None:
    return rag_runtime.create_reranker(settings)


RerankerDep = Annotated[Reranker | None, Depends(get_reranker)]


@lru_cache
def get_pdf_extractor() -> PDFExtractor:
    return rag_runtime.create_pdf_extractor(settings)


PDFExtractorDep = Annotated[PDFExtractor, Depends(get_pdf_extractor)]


@lru_cache
def get_chunking_registry() -> ChunkingStrategyRegistry:
    return rag_runtime.create_chunking_registry()


ChunkingRegistryDep = Annotated[ChunkingStrategyRegistry, Depends(get_chunking_registry)]


@lru_cache
def get_rag_ingestion_service() -> RAGIngestionService:
    return build_rag_ingestion_service(
        vector_store=get_vector_store(),
        keyword_index=get_keyword_index() if settings.bm25_enabled else None,
    )


def build_rag_ingestion_service(
    *, vector_store: VectorStore, keyword_index: KeywordIndex | None,
) -> RAGIngestionService:
    return rag_runtime.build_rag_ingestion_service(
        settings, vector_store=vector_store, keyword_index=keyword_index,
        embedding=get_embedding_provider(), registry=get_chunking_registry(), pdf_extractor=get_pdf_extractor(),
    )


RAGIngestionServiceDep = Annotated[RAGIngestionService, Depends(get_rag_ingestion_service)]


@lru_cache
def get_rag_retrieval_service() -> RAGRetrievalService:
    return build_rag_retrieval_service(
        vector_store=get_vector_store(),
        keyword_index=get_keyword_index() if settings.bm25_enabled else None,
    )


def build_rag_retrieval_service(
    *, vector_store: VectorStore, keyword_index: KeywordIndex | None,
) -> RAGRetrievalService:
    return rag_runtime.build_rag_retrieval_service(
        settings, vector_store=vector_store, keyword_index=keyword_index,
        embedding=get_embedding_provider(), reranker=get_reranker(),
    )


RAGRetrievalServiceDep = Annotated[RAGRetrievalService, Depends(get_rag_retrieval_service)]


def get_langgraph_rag_service(
    retrieval_service: RAGRetrievalServiceDep, llm: LLMDep,
) -> LangGraphRAGService:
    return rag_runtime.build_langgraph_rag_service(settings, retrieval_service, llm)


LangGraphRAGServiceDep = Annotated[LangGraphRAGService, Depends(get_langgraph_rag_service)]


@lru_cache
def get_retriever_tool() -> RetrieverTool:
    return RetrieverTool(
        retrieval_service=get_rag_retrieval_service(),
        default_top_k=settings.rag_top_k,
    )


@lru_cache
def get_tool_registry() -> ToolRegistry:
    return ToolRegistry([PingTool(), get_retriever_tool()])


ToolRegistryDep = Annotated[ToolRegistry, Depends(get_tool_registry)]


def get_agent_service(llm: LLMDep, registry: ToolRegistryDep) -> AgentService:
    return AgentService(
        llm=llm,
        registry=registry,
        max_steps=settings.agent_max_steps,
        temperature=settings.agent_temperature,
        max_tokens=settings.agent_max_tokens,
        timeout_s=settings.agent_timeout_s,
        system_prompt=settings.agent_system_prompt,
    )


AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]


def get_agent_ask_pipeline(
    agent_service: AgentServiceDep,
    llm: LLMDep,
    query_refinement_service: QueryRefinementServiceDep,
    embedding_provider: EmbeddingProviderDep,
    semantic_cache_service: SemanticCacheServiceDep,
    documents_repository: DocumentsRepositoryDep,
    permission_service: PermissionServiceDep,
) -> AgentAskPipeline:
    return AgentAskPipeline(
        agent_service=agent_service,
        llm=llm,
        query_refinement_service=query_refinement_service,
        embedding_provider=embedding_provider,
        semantic_cache_service=semantic_cache_service,
        documents_repository=documents_repository,
        permission_service=permission_service,
    )


AgentAskPipelineDep = Annotated[AgentAskPipeline, Depends(get_agent_ask_pipeline)]


@lru_cache
def get_evaluation_retriever() -> EvaluationRetriever:
    return RAGRetrievalEvaluatorAdapter(
        retrieval_service=get_rag_retrieval_service(),
    )


@lru_cache
def get_evaluation_judge() -> ContextRelevanceJudge | None:
    if not settings.evaluation_judge_enabled:
        return None
    if not settings.openai_key:
        raise RuntimeError("Missing OPENAI_KEY in environment.")
    if not settings.evaluation_judge_model:
        raise RuntimeError("Missing EVALUATION_JUDGE_MODEL in environment.")

    llm = OpenAILLM(
        api_key=settings.openai_key,
        model=settings.evaluation_judge_model,
        base_url=settings.evaluation_judge_base_url or settings.ollama_base_url,
        timeout_s=settings.external_request_timeout_s,
        max_retries=settings.external_max_retries,
    )
    return ContextRelevanceJudge(
        llm=llm,
        max_tokens=settings.evaluation_judge_max_tokens,
        temperature=settings.evaluation_judge_temperature,
        timeout_s=settings.evaluation_judge_timeout_s,
    )


@lru_cache
def get_retrieval_evaluation_service() -> RetrievalEvaluationService:
    return RetrievalEvaluationService(
        session_factory=AsyncSessionFactory,
        dataset_storage_dir=settings.evaluation_data_dir,
        retriever_factory=get_evaluation_retriever,
        judge_factory=get_evaluation_judge,
    )


RetrievalEvaluationServiceDep = Annotated[
    RetrievalEvaluationService, Depends(get_retrieval_evaluation_service)
]
