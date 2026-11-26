from __future__ import annotations

import asyncio
from asyncio import Lock, Semaphore
from contextlib import asynccontextmanager
from dataclasses import asdict
from hmac import compare_digest
import json
import os
from pathlib import Path
import re
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pdfminer.psparser import PSException
from pdfplumber.utils.exceptions import PdfminerException
from pydantic import BaseModel, Field, StringConstraints, model_validator

from src.infrastructure.knowledge_search import (
    PostgresKeywordIndex,
    PostgresVectorStore,
    RetiredIndexError,
    initialize_knowledge_search,
    purge_knowledge_version,
    supports_hnsw_iterative_scan,
)
from src.infrastructure.keyword.sqlite_bm25 import SQLiteBM25Index
from src.infrastructure.vector_db.chroma_vectorstore import ChromaVectorStore
from src.rag import runtime
from src.rag.keyword import KeywordIndex
from src.rag.models import RetrievalFilter
from src.rag.vectorstore.interface import VectorStore
from src.settings.rag_runtime import BASE_DIR, get_rag_settings

settings = get_rag_settings()


class ScopedRequest(BaseModel):
    index_id: UUID = Field(alias="indexId")


class IndexRequest(ScopedRequest):
    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000)]
    document_type: str | None = Field(default=None, alias="documentType")
    product_name: str | None = Field(default=None, alias="productName")
    product_version: str | None = Field(default=None, alias="productVersion")

    def metadata(self) -> dict[str, str | None] | None:
        raw = {
            "document_type": self.document_type,
            "product_name": self.product_name,
            "product_version": self.product_version,
        }
        cleaned: dict[str, str | None] = {k: v.strip() for k, v in raw.items() if v and v.strip()}
        return cleaned or None


class QueryRequest(BaseModel):
    index_id: UUID | None = Field(default=None, alias="indexId")
    index_ids: list[UUID] | None = Field(default=None, alias="indexIds")
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]
    document_types: list[str] | None = Field(default=None, alias="documentTypes")
    product_name: str | None = Field(default=None, alias="productName")
    product_version: str | None = Field(default=None, alias="productVersion")

    @model_validator(mode="after")
    def require_one_scope(self):
        if (self.index_id is None) == (self.index_ids is None):
            raise ValueError("Supply exactly one index scope")
        return self

    def filters(self) -> RetrievalFilter:
        ids = (str(self.index_id),) if self.index_id is not None else tuple(map(str, self.index_ids or []))
        doc_types = tuple(t.strip() for t in (self.document_types or []) if t and t.strip())
        product = self.product_name.strip() if self.product_name and self.product_name.strip() else None
        version = self.product_version.strip() if self.product_version and self.product_version.strip() else None
        return RetrievalFilter(
            allowed_doc_ids=ids,
            document_types=doc_types,
            product_name=product,
            product_version=version,
            deny_all=not ids,
        )

    def answer_filters(self) -> dict[str, object]:
        resolved = self.filters()
        return {
            "allowed_doc_ids": resolved.allowed_doc_ids,
            "document_types": resolved.document_types,
            "product_name": resolved.product_name,
            "product_version": resolved.product_version,
            "deny_all": resolved.deny_all,
        }


def local_indexes(directory: Path) -> tuple[ChromaVectorStore, SQLiteBM25Index]:
    return (
        ChromaVectorStore(
            persist_dir=str(directory / "chroma"),
            collection_name=settings.resolved_rag_collection_name(),
        ),
        SQLiteBM25Index(database_path=str(directory / "bm25.db")),
    )


def positive_env(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value < 1:
        raise RuntimeError(f"{name} must be positive")
    return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    token = os.environ.get("KNOWLEDGE_INDEX_TOKEN", "")
    if len(token.strip()) < 32:
        raise RuntimeError("KNOWLEDGE_INDEX_TOKEN must contain at least 32 characters")
    if not settings.dense_enabled or not settings.bm25_enabled:
        raise RuntimeError("Document publication requires both vector and keyword indexes")
    app.state.authorization = f"Bearer {token}".encode()
    app.state.index_slots = Semaphore(positive_env("KNOWLEDGE_INDEX_CONCURRENCY", 4))
    app.state.search_slots = Semaphore(positive_env("KNOWLEDGE_SEARCH_CONCURRENCY", 40))
    app.state.ask_slots = Semaphore(positive_env("KNOWLEDGE_ASK_CONCURRENCY", 8))
    pool = None
    try:
        postgres = os.environ.get("KNOWLEDGE_SEARCH_DATABASE_URL")
        vector: VectorStore
        keyword: KeywordIndex
        if postgres:
            pool = await asyncpg.create_pool(
                postgres, min_size=1, max_size=positive_env("KNOWLEDGE_SEARCH_POOL_SIZE", 12),
                timeout=5, command_timeout=5,
            )
            await initialize_knowledge_search(pool)
            hnsw_enabled = os.environ.get("KNOWLEDGE_SEARCH_HNSW_ENABLED", "true").strip().lower() not in {
                "0", "false", "no", "off",
            }
            iterative_scan_supported = await supports_hnsw_iterative_scan(pool) if hnsw_enabled else False
            vector = PostgresVectorStore(
                pool,
                settings.resolved_rag_collection_name(),
                hnsw_enabled=hnsw_enabled,
                hnsw_min_docs=positive_env("KNOWLEDGE_SEARCH_HNSW_MIN_DOCS", 32),
                hnsw_ef_search=positive_env("KNOWLEDGE_SEARCH_HNSW_EF_SEARCH", 64),
                hnsw_iterative_scan=os.environ.get("KNOWLEDGE_SEARCH_HNSW_ITERATIVE_SCAN", "relaxed_order").strip(),
                supports_iterative_scan=iterative_scan_supported,
            )
            keyword = PostgresKeywordIndex(pool)
        else:
            directory = Path(os.environ.get("KNOWLEDGE_INDEX_DIR", str(BASE_DIR / "data/knowledge-index")))
            vector, keyword = local_indexes(directory)
            app.state.retired = directory / "retired"
            app.state.retired.mkdir(parents=True, exist_ok=True)
            app.state.mutation_locks = tuple(Lock() for _ in range(64))
        app.state.pool = pool
        app.state.vector = vector
        app.state.keyword = keyword
        embedding = runtime.create_embedding_provider(settings)
        app.state.ingestion = runtime.build_rag_ingestion_service(
            settings, embedding=embedding, vector_store=vector, keyword_index=keyword,
            registry=runtime.create_chunking_registry(), pdf_extractor=runtime.create_pdf_extractor(settings),
        )
        app.state.retrieval = runtime.build_rag_retrieval_service(
            settings, embedding=embedding, vector_store=vector, keyword_index=keyword,
            reranker=runtime.create_reranker(settings),
        )
        app.state.answers = runtime.build_langgraph_rag_service(
            settings, app.state.retrieval, runtime.create_llm(settings),
        )
        yield
    finally:
        if pool is not None:
            await pool.close()


@asynccontextmanager
async def capacity(semaphore: Semaphore):
    if semaphore.locked():
        raise HTTPException(503, "Index service capacity reached", headers={"Retry-After": "1"})
    async with semaphore:
        yield


@asynccontextmanager
async def index_write(request: Request, index_id: UUID):
    async with capacity(request.app.state.index_slots):
        state = request.app.state
        if state.pool is not None:
            yield
        else:
            async with state.mutation_locks[index_id.int % len(state.mutation_locks)]:
                if (state.retired / str(index_id)).exists():
                    raise RetiredIndexError("Document version was retired")
                yield


async def require_index_token(
    request: Request, authorization: Annotated[str | None, Header()] = None,
) -> None:
    if authorization is None or not compare_digest(
        request.app.state.authorization, authorization.encode(),
    ):
        raise HTTPException(401, "Invalid service token", headers={"WWW-Authenticate": "Bearer"})


app = FastAPI(
    title="Knowledge Index",
    lifespan=lifespan,
    dependencies=[Depends(require_index_token)],
    openapi_url=None,
)


@app.exception_handler(RetiredIndexError)
async def retired_index(request: Request, error: RetiredIndexError):
    return JSONResponse(status_code=410, content={"detail": str(error)})


@app.post("/internal/index")
async def index_version(payload: IndexRequest, request: Request):
    async with index_write(request, payload.index_id):
        result = await request.app.state.ingestion.ingest_text(
            text=payload.text,
            source=payload.source,
            doc_id=str(payload.index_id),
            metadata=payload.metadata(),
        )
    if result.chunks_ingested == 0:
        raise HTTPException(422, "Document did not produce any chunks")
    return {"chunksIndexed": result.chunks_ingested}


@app.post("/internal/index/pdf")
async def index_pdf_version(
    request: Request,
    index_id: Annotated[UUID, Form(alias="indexId")],
    source: Annotated[str, Form(min_length=1, max_length=255)],
    file: Annotated[UploadFile, File()],
    document_type: Annotated[str | None, Form(alias="documentType")] = None,
    product_name: Annotated[str | None, Form(alias="productName")] = None,
    product_version: Annotated[str | None, Form(alias="productVersion")] = None,
):
    limit = settings.rag_pdf_max_mb * 1024 * 1024
    raw_meta = {
        "document_type": document_type,
        "product_name": product_name,
        "product_version": product_version,
    }
    metadata = {k: v.strip() for k, v in raw_meta.items() if v and v.strip()} or None
    try:
        async with index_write(request, index_id):
            contents = await file.read(limit + 1)
            if len(contents) > limit:
                raise HTTPException(413, "PDF exceeds the configured size limit")
            try:
                result = await request.app.state.ingestion.ingest_pdf(
                    pdf_bytes=contents, doc_id=str(index_id), source=source, metadata=metadata,
                )
            except (PdfminerException, PSException) as error:
                raise HTTPException(422, "PDF could not be parsed") from error
            except ValueError as error:
                if isinstance(error, RetiredIndexError):
                    raise
                raise HTTPException(422, str(error)) from error
    finally:
        await file.close()
    return {"chunksIndexed": result.chunks_ingested, "pagesIndexed": result.pages_ingested}


@app.post("/internal/index/purge")
async def purge_index_version(payload: ScopedRequest, request: Request):
    state = request.app.state
    doc_id = str(payload.index_id)
    async with capacity(state.index_slots):
        if state.pool is not None:
            await purge_knowledge_version(state.pool, doc_id)
        else:
            async with state.mutation_locks[payload.index_id.int % len(state.mutation_locks)]:
                (state.retired / doc_id).touch(exist_ok=True)
                await state.vector.delete_by_doc_id(doc_id=doc_id)
                await state.keyword.delete_by_doc_id(doc_id=doc_id)
    return {"purged": True}


@app.post("/internal/search")
async def search_version(payload: QueryRequest, request: Request):
    async with capacity(request.app.state.search_slots):
        chunks = await request.app.state.retrieval.retrieve(
            query=payload.query,
            filters=payload.filters(),
        )
    return {"chunks": [asdict(chunk) for chunk in chunks]}


@app.post("/internal/ask")
async def ask_version(payload: QueryRequest, request: Request):
    answer_filters = payload.answer_filters()
    async with capacity(request.app.state.ask_slots):
        result = await request.app.state.answers.run(
            question=payload.query,
            filters=answer_filters,
        )
    return asdict(result)


_CITATION_TOKEN_RE = re.compile(r"(\[[^\]\r\n]+\])")


def _iter_answer_deltas(answer: str, step: int = 4):
    """Yield small chunks while keeping [doc_id:chunk_id] citation markers atomic."""
    for part in _CITATION_TOKEN_RE.split(answer):
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            yield part
        else:
            for start in range(0, len(part), step):
                yield part[start:start + step]


@app.post("/internal/ask/stream")
async def ask_version_stream(payload: QueryRequest, request: Request):
    answer_filters = payload.answer_filters()
    semaphore = request.app.state.ask_slots
    if semaphore.locked():
        raise HTTPException(503, "Index service capacity reached", headers={"Retry-After": "1"})

    async def event_stream():
        async with semaphore:
            async for event, data in request.app.state.answers.stream(
                question=payload.query,
                filters=answer_filters,
            ):
                if event == "answer_completed" and isinstance(data.get("answer"), str):
                    citations = data.get("citations") or []
                    yield f"event: answer_sources\ndata: {json.dumps({'citations': citations}, ensure_ascii=False)}\n\n"
                    for delta in _iter_answer_deltas(data["answer"]):
                        yield f"event: answer_delta\ndata: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
                        await asyncio.sleep(0.018)
                encoded = json.dumps(data, ensure_ascii=False)
                yield f"event: {event}\ndata: {encoded}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/internal/health")
async def health(request: Request):
    if request.app.state.pool is not None:
        await request.app.state.pool.fetchval("SELECT 1", timeout=2)
    return {"status": "UP"}
