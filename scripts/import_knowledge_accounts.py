"""Copy legacy FastAPI account IDs and password hashes into the Java catalog."""

from __future__ import annotations

import asyncio
import os

import asyncpg


async def import_accounts(source_url: str, target_url: str) -> tuple[int, int]:
    source = await asyncpg.connect(source_url, timeout=5)
    try:
        target = await asyncpg.connect(target_url, timeout=5)
        try:
            return await copy_accounts(source, target)
        finally:
            await target.close()
    finally:
        await source.close()


async def copy_accounts(source: asyncpg.Connection, target: asyncpg.Connection) -> tuple[int, int]:
    imported = 0
    existing = 0
    async with source.transaction(readonly=True, isolation="repeatable_read"), target.transaction():
        rows = source.cursor("""
            SELECT id, email, hashed_password, is_active, is_superuser, is_verified,
                   to_jsonb(u)->>'department' AS department
            FROM public."user" u ORDER BY id
        """, prefetch=100)
        async for row in rows:
            email = row["email"].lower()
            digest = row["hashed_password"]
            if not digest.startswith(("$argon2id$", "$argon2i$", "$argon2d$", "$2a$", "$2b$", "$2y$")):
                raise ValueError(f"Account {row['id']} has an unsupported password hash")
            created = await target.fetchval("""
                INSERT INTO knowledge_backend.accounts
                    (id, email, password_hash, is_active, is_admin, is_verified, department)
                VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT DO NOTHING RETURNING id
            """, row["id"], email, digest, row["is_active"], row["is_superuser"], row["is_verified"], row["department"])
            if created:
                imported += 1
                continue
            saved_email = await target.fetchval("SELECT email FROM knowledge_backend.accounts WHERE id = $1", row["id"])
            if saved_email != email:
                raise ValueError(f"Account {row['id']} conflicts with the Java account catalog")
            existing += 1
    return imported, existing


if __name__ == "__main__":
    legacy = os.environ.get("KNOWLEDGE_LEGACY_DATABASE_URL")
    catalog = os.environ.get("KNOWLEDGE_CATALOG_DATABASE_URL")
    if not legacy or not catalog:
        raise SystemExit("KNOWLEDGE_LEGACY_DATABASE_URL and KNOWLEDGE_CATALOG_DATABASE_URL are required")
    imported, existing = asyncio.run(import_accounts(legacy, catalog))
    print(f"Imported accounts: {imported}. Existing accounts: {existing}.")
