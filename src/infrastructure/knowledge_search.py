from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re
from typing import Any

import asyncpg

from src.rag.keyword import KeywordIndex
from src.rag.models import RAGChunk, RetrievalFilter, RetrievedChunk
from src.rag.vectorstore.interface import VectorStore

_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")
_VALID_ITERATIVE_SCAN_MODES = frozenset({"off", "relaxed_order", "strict_order"})
_HNSW_MAX_DIMENSIONS = 2000


def _hnsw_index_name(model: str, dim: int) -> str:
    digest = hashlib.sha256(f"{model}:{dim}".encode()).hexdigest()[:16]
    return f"knowledge_dense_hnsw_{dim}_{digest}"


async def supports_hnsw_iterative_scan(pool: asyncpg.Pool) -> bool:
    async with pool.acquire() as connection:
        return bool(await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_settings WHERE name = 'hnsw.iterative_scan')"
        ))


async def initialize_knowledge_search(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(846201539)")
        if not await connection.fetchval("SELECT EXISTS (SELECT FROM pg_extension WHERE extname = 'vector')"):
            await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
        if not await connection.fetchval("SELECT to_regnamespace('knowledge_search') IS NOT NULL"):
            await connection.execute("CREATE SCHEMA IF NOT EXISTS knowledge_search")
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_search.dense_chunks (
                model text NOT NULL,
                doc_id text NOT NULL,
                chunk_id text NOT NULL,
                payload jsonb NOT NULL,
                embedding vector NOT NULL,
                PRIMARY KEY (model, doc_id, chunk_id)
            );
            CREATE TABLE IF NOT EXISTS knowledge_search.lexical_chunks (
                doc_id text NOT NULL,
                chunk_id text NOT NULL,
                payload jsonb NOT NULL,
                terms tsvector GENERATED ALWAYS AS (to_tsvector('simple', payload->>'text')) STORED,
                PRIMARY KEY (doc_id, chunk_id)
            );
            CREATE INDEX IF NOT EXISTS knowledge_lexical_terms
                ON knowledge_search.lexical_chunks USING gin (terms);
            CREATE TABLE IF NOT EXISTS knowledge_search.retired_versions (
                doc_id text PRIMARY KEY
            );
        """)


class RetiredIndexError(ValueError):
    pass


async def _lock_version(connection: asyncpg.Connection, doc_id: str) -> None:
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 7138845))", doc_id)


async def _guard_index_writes(connection: asyncpg.Connection, chunks: list[RAGChunk]) -> None:
    ids = sorted({chunk.doc_id for chunk in chunks})
    for doc_id in ids:
        await _lock_version(connection, doc_id)
    if await connection.fetchval("""
        SELECT EXISTS(SELECT 1 FROM knowledge_search.retired_versions WHERE doc_id = ANY($1::text[]))
    """, ids):
        raise RetiredIndexError("Document version was retired")


async def purge_knowledge_version(pool: asyncpg.Pool, doc_id: str) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await _lock_version(connection, doc_id)
        await connection.execute("""
            INSERT INTO knowledge_search.retired_versions (doc_id) VALUES ($1) ON CONFLICT DO NOTHING
        """, doc_id)
        await connection.execute("DELETE FROM knowledge_search.dense_chunks WHERE doc_id = $1", doc_id)
        await connection.execute("DELETE FROM knowledge_search.lexical_chunks WHERE doc_id = $1", doc_id)


def _scope(filters: RetrievalFilter | None, args: list[Any], doc_id: str | None = None) -> str:
    # Column names are fixed here. Every caller-provided value is a bound SQL argument.
    if filters is not None and filters.deny_all:
        return "FALSE"
    conditions = []
    if doc_id:
        args.append(doc_id)
        conditions.append(f"doc_id = ${len(args)}")
    if filters is not None:
        if filters.allowed_doc_ids:
            args.append(list(filters.allowed_doc_ids))
            conditions.append(f"doc_id = ANY(${len(args)}::text[])")
        for field in ("product_name", "product_version", "knowledge_space_id", "department"):
            value = getattr(filters, field)
            if value:
                args.append(value)
                conditions.append(f"payload->>'{field}' = ${len(args)}")
        if filters.document_types:
            args.append(list(filters.document_types))
            conditions.append(f"payload->>'document_type' = ANY(${len(args)}::text[])")
    return " AND ".join(conditions) or "TRUE"


def _retrieved(row: asyncpg.Record, source: str) -> RetrievedChunk:
    chunk = json.loads(row["payload"])
    for field in ("chunking_strategy", "chunk_size", "chunk_overlap"):
        chunk.pop(field)
    score = float(row["score"])
    return RetrievedChunk(**chunk, score=score, retrieval_sources=(source,), raw_scores={source: score})


def _vector(embedding: list[float]) -> str:
    return json.dumps(embedding, allow_nan=False)


class PostgresVectorStore(VectorStore):
    def __init__(
        self,
        pool: asyncpg.Pool,
        model: str,
        *,
        hnsw_enabled: bool = True,
        hnsw_min_docs: int = 32,
        hnsw_ef_search: int = 64,
        hnsw_iterative_scan: str = "relaxed_order",
        supports_iterative_scan: bool = False,
    ) -> None:
        if hnsw_iterative_scan not in _VALID_ITERATIVE_SCAN_MODES:
            raise ValueError(f"Unsupported hnsw_iterative_scan mode: {hnsw_iterative_scan}")
        self._pool = pool
        self._model = model
        self._hnsw_enabled = hnsw_enabled and bool(_MODEL_NAME_RE.fullmatch(model))
        self._hnsw_min_docs = max(1, hnsw_min_docs)
        self._hnsw_ef_search = max(1, hnsw_ef_search)
        self._hnsw_iterative_scan = hnsw_iterative_scan
        self._supports_iterative_scan = supports_iterative_scan
        self._ensured_hnsw_dims: set[int] = set()

    def _should_use_hnsw(self, *, doc_id: str | None, filters: RetrievalFilter | None) -> bool:
        if not self._hnsw_enabled or doc_id is not None:
            return False
        if filters is not None:
            if filters.deny_all:
                return False
            if filters.allowed_doc_ids and len(filters.allowed_doc_ids) < self._hnsw_min_docs:
                return False
        return True

    async def _ensure_hnsw_index(self, connection: asyncpg.Connection, dim: int) -> bool:
        if not self._hnsw_enabled or dim <= 0 or dim > _HNSW_MAX_DIMENSIONS:
            return False
        if dim in self._ensured_hnsw_dims:
            return True
        index_name = _hnsw_index_name(self._model, dim)
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 912341))", index_name)
        await connection.execute(f"""
            CREATE INDEX IF NOT EXISTS {index_name}
                ON knowledge_search.dense_chunks
                USING hnsw ((embedding::vector({dim})) vector_l2_ops)
                WHERE model = '{self._model}'
        """)
        self._ensured_hnsw_dims.add(dim)
        return True

    async def upsert_chunks(self, *, chunks: list[RAGChunk], embeddings: list[list[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("Expected an embedding per chunk")
        async with self._pool.acquire() as connection, connection.transaction():
            await _guard_index_writes(connection, chunks)
            await connection.executemany("""
                INSERT INTO knowledge_search.dense_chunks (model, doc_id, chunk_id, payload, embedding)
                VALUES ($1, $2, $3, $4::jsonb, $5::text::vector)
                ON CONFLICT (model, doc_id, chunk_id) DO UPDATE
                SET payload = EXCLUDED.payload, embedding = EXCLUDED.embedding
            """, [
                (self._model, chunk.doc_id, chunk.chunk_id, json.dumps(asdict(chunk)), _vector(vector))
                for chunk, vector in zip(chunks, embeddings)
            ])
            if embeddings:
                await self._ensure_hnsw_index(connection, len(embeddings[0]))

    async def similarity_search(
        self, *, query_embedding: list[float], top_k: int, doc_id: str | None = None,
        filters: RetrievalFilter | None = None,
    ) -> list[RetrievedChunk]:
        dim = len(query_embedding)
        if 0 < dim <= _HNSW_MAX_DIMENSIONS and self._should_use_hnsw(doc_id=doc_id, filters=filters):
            args: list[Any] = [_vector(query_embedding), top_k]
            scope = _scope(filters, args, doc_id)
            async with self._pool.acquire() as connection, connection.transaction():
                await self._ensure_hnsw_index(connection, dim)
                await connection.execute(f"SET LOCAL hnsw.ef_search = {self._hnsw_ef_search}")
                if self._supports_iterative_scan and self._hnsw_iterative_scan != "off":
                    await connection.execute(f"SET LOCAL hnsw.iterative_scan = '{self._hnsw_iterative_scan}'")
                rows = await connection.fetch(f"""
                    SELECT payload, 1.0 / (1.0 + distance * distance) AS score
                    FROM (
                        SELECT chunk_id, payload,
                               (embedding::vector({dim})) <-> $1::text::vector({dim}) AS distance
                        FROM knowledge_search.dense_chunks
                        WHERE model = '{self._model}' AND ({scope})
                        ORDER BY (embedding::vector({dim})) <-> $1::text::vector({dim}), chunk_id
                        LIMIT $2
                    ) closest ORDER BY score DESC, chunk_id
                """, *args)
            return [_retrieved(row, "dense") for row in rows]

        args = [self._model, _vector(query_embedding), top_k]
        scope = _scope(filters, args, doc_id)
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(f"""
                WITH candidates AS MATERIALIZED (
                    SELECT chunk_id, payload, embedding
                    FROM knowledge_search.dense_chunks WHERE model = $1 AND ({scope})
                )
                SELECT payload, 1.0 / (1.0 + distance * distance) AS score
                FROM (
                    SELECT chunk_id, payload, embedding <-> $2::text::vector AS distance
                    FROM candidates ORDER BY distance, chunk_id LIMIT $3
                ) closest ORDER BY score DESC, chunk_id
            """, *args)
        return [_retrieved(row, "dense") for row in rows]

    async def list_chunks(self, *, doc_id: str) -> list[RAGChunk]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch("""
                SELECT payload FROM knowledge_search.dense_chunks
                WHERE model = $1 AND doc_id = $2 ORDER BY chunk_id
            """, self._model, doc_id)
        return [RAGChunk(**json.loads(row["payload"])) for row in rows]

    async def delete_by_doc_id(self, *, doc_id: str) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute("""
                DELETE FROM knowledge_search.dense_chunks WHERE model = $1 AND doc_id = $2
            """, self._model, doc_id)


class PostgresKeywordIndex(KeywordIndex):
    source = "postgres_fts"

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_chunks(self, *, chunks: list[RAGChunk]) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            await _guard_index_writes(connection, chunks)
            await connection.executemany("""
                INSERT INTO knowledge_search.lexical_chunks (doc_id, chunk_id, payload)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (doc_id, chunk_id) DO UPDATE SET payload = EXCLUDED.payload
            """, [(chunk.doc_id, chunk.chunk_id, json.dumps(asdict(chunk))) for chunk in chunks])

    async def search(
        self, *, query: str, top_k: int, filters: RetrievalFilter | None = None,
    ) -> list[RetrievedChunk]:
        args: list[Any] = [query, top_k]
        scope = _scope(filters, args)
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(f"""
                WITH parsed AS (
                    SELECT string_agg(quote_literal(term), ' | ')::tsquery AS query
                    FROM unnest(tsvector_to_array(to_tsvector('simple', $1))) AS term
                )
                SELECT payload, ts_rank_cd(terms, parsed.query) AS score
                FROM knowledge_search.lexical_chunks CROSS JOIN parsed
                WHERE terms @@ parsed.query AND ({scope}) ORDER BY score DESC, chunk_id LIMIT $2
            """, *args)
        return [_retrieved(row, self.source) for row in rows]

    async def delete_by_doc_id(self, *, doc_id: str) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute("DELETE FROM knowledge_search.lexical_chunks WHERE doc_id = $1", doc_id)
