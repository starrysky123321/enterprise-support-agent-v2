"""Rebuild all published versions in a replacement index service before cutover."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import UUID

import asyncpg
import httpx


async def rebuild(database_url: str, index_url: str, token: str, blob_dir: Path | None = None) -> int:
    connection = await asyncpg.connect(database_url, timeout=5)
    indexed = 0
    try:
        supports_pdf = await connection.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'knowledge_backend' AND table_name = 'document_versions'
                  AND column_name = 'content_type'
            )
        """)
        supports_storage_key = await connection.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'knowledge_backend' AND table_name = 'document_versions'
                  AND column_name = 'pdf_storage_key'
            )
        """)
        type_column = "v.content_type" if supports_pdf else "'TEXT' AS content_type"
        storage_column = "pdf_storage_key" if supports_storage_key else "NULL::text AS pdf_storage_key"
        async with httpx.AsyncClient(
            base_url=index_url, timeout=65, headers={"Authorization": f"Bearer {token}"},
        ) as client:
            after = UUID(int=0)
            while True:
                rows = await connection.fetch(f"""
                    SELECT v.id, v.document_id, v.body, {type_column}
                    FROM knowledge_backend.documents d
                    JOIN knowledge_backend.document_versions v
                        ON v.workspace_id = d.workspace_id AND v.document_id = d.id
                        AND v.version_number = d.published_version
                    WHERE v.id > $1 ORDER BY v.id LIMIT 100
                """, after)
                if not rows:
                    return indexed
                for row in rows:
                    index_id = str(row["id"])
                    if row["content_type"] == "application/pdf":
                        pdf = await connection.fetchrow(f"""
                            SELECT filename, pdf, {storage_column}
                            FROM knowledge_backend.document_versions WHERE id = $1
                        """, row["id"])
                        pdf_bytes = pdf["pdf"]
                        if pdf_bytes is None and pdf["pdf_storage_key"]:
                            if blob_dir is None:
                                raise RuntimeError("KNOWLEDGE_PDF_STORAGE_DIR is required to rebuild external PDFs")
                            blob_path = (blob_dir / pdf["pdf_storage_key"]).resolve()
                            if not blob_path.is_relative_to(blob_dir.resolve()):
                                raise RuntimeError(f"Invalid pdf_storage_key: {pdf['pdf_storage_key']}")
                            pdf_bytes = blob_path.read_bytes()
                        result = await client.post(
                            "/internal/index/pdf", data={"indexId": index_id, "source": pdf["filename"]},
                            files={"file": (pdf["filename"], pdf_bytes, "application/pdf")},
                        )
                    else:
                        result = await client.post("/internal/index", json={
                            "indexId": index_id, "source": row["document_id"], "text": row["body"],
                        })
                    if result.raise_for_status().json()["chunksIndexed"] < 1:
                        raise RuntimeError(f"Version {row['id']} returned no chunks")
                    indexed += 1
                after = rows[-1]["id"]
    finally:
        await connection.close()


if __name__ == "__main__":
    database_url = os.environ.get("KNOWLEDGE_CATALOG_DATABASE_URL")
    index_url = os.environ.get("KNOWLEDGE_INDEX_URL", "http://127.0.0.1:8011")
    index_token = os.environ.get("KNOWLEDGE_INDEX_TOKEN")
    storage_dir = os.environ.get("KNOWLEDGE_PDF_STORAGE_DIR")
    if not database_url or not index_token:
        raise SystemExit("KNOWLEDGE_CATALOG_DATABASE_URL and KNOWLEDGE_INDEX_TOKEN are required")
    print(f"Indexed published versions: {asyncio.run(rebuild(database_url, index_url, index_token, Path(storage_dir) if storage_dir else None))}")
