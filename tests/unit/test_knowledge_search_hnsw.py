from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json

from src.infrastructure.knowledge_search import PostgresVectorStore, _hnsw_index_name
from src.rag.models import RAGChunk, RetrievalFilter


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.fetched: list[tuple[str, tuple[object, ...]]] = []
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))

    async def executemany(self, sql: str, rows):
        self.executemany_calls.append((sql, list(rows)))

    async def fetchval(self, sql: str, *args):
        return False

    async def fetch(self, sql: str, *args):
        self.fetched.append((sql, args))
        payload = json.dumps({
            "doc_id": "doc-1",
            "chunk_id": "c-1",
            "text": "timeout is 30 seconds",
            "source": "guide.pdf",
            "chunking_strategy": "fixed",
            "chunk_size": 500,
            "chunk_overlap": 50,
        })
        return [{"payload": payload, "score": 0.9}]


class _FakePool:
    def __init__(self) -> None:
        self.connection = _FakeConnection()

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def test_upsert_creates_partial_hnsw_index_once_per_dimension():
    async def _run():
        pool = _FakePool()
        store = PostgresVectorStore(pool, "local-hash-384-v1", hnsw_enabled=True)
        chunk = RAGChunk(
            doc_id="doc-1",
            chunk_id="c-1",
            text="hello",
            source="guide",
            chunking_strategy="fixed",
            chunk_size=500,
            chunk_overlap=50,
        )

        await store.upsert_chunks(chunks=[chunk], embeddings=[[0.1, 0.2, 0.3]])
        await store.upsert_chunks(chunks=[chunk], embeddings=[[0.1, 0.2, 0.3]])

        ddl_calls = [sql for sql, _ in pool.connection.executed if "CREATE INDEX IF NOT EXISTS" in sql]
        assert len(ddl_calls) == 1
        expected_name = _hnsw_index_name("local-hash-384-v1", 3)
        assert expected_name in ddl_calls[0]
        assert "USING hnsw ((embedding::vector(3)) vector_l2_ops)" in ddl_calls[0]
        assert "WHERE model = 'local-hash-384-v1'" in ddl_calls[0]

    asyncio.run(_run())


def test_similarity_search_uses_materialized_exact_scan_for_small_scope():
    async def _run():
        pool = _FakePool()
        store = PostgresVectorStore(
            pool,
            "local-hash-384-v1",
            hnsw_enabled=True,
            hnsw_min_docs=4,
            supports_iterative_scan=True,
        )

        results = await store.similarity_search(
            query_embedding=[0.1, 0.2, 0.3],
            top_k=5,
            filters=RetrievalFilter(allowed_doc_ids=("doc-1", "doc-2")),
        )

        assert len(results) == 1
        assert len(pool.connection.fetched) == 1
        sql, args = pool.connection.fetched[0]
        assert "WITH candidates AS MATERIALIZED" in sql
        assert args[0] == "local-hash-384-v1"
        assert not any("hnsw.ef_search" in stmt for stmt, _ in pool.connection.executed)

    asyncio.run(_run())


def test_similarity_search_uses_hnsw_and_iterative_scan_for_large_workspace():
    async def _run():
        pool = _FakePool()
        store = PostgresVectorStore(
            pool,
            "bge-small-en-v1.5",
            hnsw_enabled=True,
            hnsw_min_docs=4,
            hnsw_ef_search=80,
            hnsw_iterative_scan="relaxed_order",
            supports_iterative_scan=True,
        )

        doc_ids = tuple(f"doc-{i}" for i in range(6))
        results = await store.similarity_search(
            query_embedding=[0.1, 0.2, 0.3, 0.4],
            top_k=5,
            filters=RetrievalFilter(allowed_doc_ids=doc_ids),
        )

        assert len(results) == 1
        executed_sql = [sql for sql, _ in pool.connection.executed]
        assert any("SET LOCAL hnsw.ef_search = 80" in stmt for stmt in executed_sql)
        assert any("SET LOCAL hnsw.iterative_scan = 'relaxed_order'" in stmt for stmt in executed_sql)
        sql, _ = pool.connection.fetched[0]
        assert "WITH candidates AS MATERIALIZED" not in sql
        assert "(embedding::vector(4)) <-> $1::text::vector(4)" in sql
        assert "WHERE model = 'bge-small-en-v1.5'" in sql

    asyncio.run(_run())


def test_similarity_search_skips_iterative_scan_when_unsupported():
    async def _run():
        pool = _FakePool()
        store = PostgresVectorStore(
            pool,
            "bge-small-en-v1.5",
            hnsw_enabled=True,
            hnsw_min_docs=2,
            supports_iterative_scan=False,
        )

        await store.similarity_search(
            query_embedding=[0.1, 0.2],
            top_k=3,
            filters=RetrievalFilter(allowed_doc_ids=("doc-1", "doc-2")),
        )

        executed_sql = [sql for sql, _ in pool.connection.executed]
        assert any("SET LOCAL hnsw.ef_search = 64" in stmt for stmt in executed_sql)
        assert not any("hnsw.iterative_scan" in stmt for stmt in executed_sql)

    asyncio.run(_run())
