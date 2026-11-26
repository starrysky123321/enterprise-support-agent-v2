import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from time import monotonic, sleep
from uuid import uuid4

import asyncpg
import httpx
import pytest


URLS = os.environ.get("KNOWLEDGE_CLUSTER_URLS", "").split()
ADMIN_TOKEN = os.environ.get("KNOWLEDGE_API_TOKEN", "")
DATABASE_URL = os.environ.get("KNOWLEDGE_CLUSTER_DB_URL", "")
QUERY = {"query": "What is the default idle timeout?"}

pytestmark = pytest.mark.skipif(not URLS or not ADMIN_TOKEN, reason="Requires a dedicated knowledge cluster")


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_concurrent_publication_is_isolated_by_workspace_and_revocation_crosses_replicas():
    suffix = uuid4().hex[:12]
    first, last = URLS[0], URLS[-1]
    route_a = f"/api/knowledge/workspaces/team-a-{suffix}"
    route_b = f"/api/knowledge/workspaces/team-b-{suffix}"
    admin = headers(ADMIN_TOKEN)
    doc_a = f"{route_a}/documents/timeout"
    doc_b = f"{route_b}/documents/timeout"
    with httpx.Client(trust_env=False) as client:
        for route in (route_a, route_b):
            client.put(first + route, headers=admin).raise_for_status()
        key_a = client.post(first + route_a + "/keys", headers=admin, json={"role": "EDITOR"}).raise_for_status().json()
        key_b = client.post(first + route_b + "/keys", headers=admin, json={"role": "EDITOR"}).raise_for_status().json()
        reader = client.post(first + route_a + "/keys", headers=admin, json={"role": "READER"}).raise_for_status().json()
        editor_a = headers(key_a["token"])
        editor_b = headers(key_b["token"])
        reader_a = headers(reader["token"])

        def submit(sequence: int) -> int:
            text = f"The default idle timeout is {30 + sequence % 8} seconds."
            response = client.post(URLS[sequence % len(URLS)] + doc_a + "/versions",
                                   headers=editor_a, json={"text": text})
            return response.raise_for_status().json()["versionNumber"]

        with ThreadPoolExecutor(max_workers=16) as workers:
            versions = list(workers.map(submit, range(48)))
        assert set(versions) == set(range(1, 9))
        other = client.post(last + doc_b + "/versions", headers=editor_b,
                            json={"text": "The default idle timeout is 777 seconds."}).raise_for_status().json()
        assert other["versionNumber"] == 1

        for url, document, version, authorization in ((first, doc_a, 8, editor_a), (last, doc_b, 1, editor_b)):
            deadline = monotonic() + 30
            while monotonic() < deadline:
                item = client.get(f"{url}{document}/versions/{version}", headers=authorization).raise_for_status().json()
                if item["publishedVersion"] == version:
                    break
                assert item["status"] != "FAILED", item
                sleep(0.05)
            else:
                pytest.fail(f"Version {version} was not published: {item}")

        assert client.post(last + doc_b + "/search", headers=reader_a, json=QUERY).status_code == 403
        assert client.post(last + doc_a + "/versions", headers=reader_a, json={"text": "blocked"}).status_code == 403
        result = client.post(last + doc_a + "/search", headers=reader_a, json=QUERY).raise_for_status().json()
        assert result["version"] == 8
        assert {"dense", "postgres_fts"} == set(result["result"]["chunks"][0]["retrieval_sources"])
        assert "777 seconds" not in result["result"]["chunks"][0]["text"]
        client.delete(first + route_a + f"/keys/{reader['id']}", headers=admin).raise_for_status()
        assert client.post(last + doc_a + "/search", headers=reader_a, json=QUERY).status_code == 401


@pytest.mark.skipif(not DATABASE_URL, reason="Requires the dedicated cluster PostgreSQL DSN")
def test_shared_index_automatically_retries_after_lexical_write_failure():
    async def verify() -> None:
        token = headers(ADMIN_TOKEN)
        base = URLS[0] + f"/api/knowledge/documents/pg-retry-{uuid4().hex[:12]}"
        async with httpx.AsyncClient(trust_env=False) as client:
            async def upload(seconds: int) -> None:
                response = await client.post(base + "/versions", headers=token,
                                             json={"text": f"The default idle timeout is {seconds} seconds."})
                response.raise_for_status()

            async def wait_for(version: int, condition) -> dict:
                deadline = monotonic() + 25
                while monotonic() < deadline:
                    response = await client.get(base + f"/versions/{version}", headers=token)
                    status = response.raise_for_status().json()
                    if condition(status):
                        return status
                    assert status["status"] != "FAILED", status
                    await asyncio.sleep(0.05)
                pytest.fail(f"Version {version} did not reach the expected state: {status}")

            await upload(30)
            await wait_for(1, lambda status: status["publishedVersion"] == 1)
            db = await asyncpg.connect(DATABASE_URL)
            try:
                async with db.transaction():
                    await db.execute("LOCK TABLE knowledge_search.lexical_chunks IN SHARE MODE")
                    await upload(60)
                    failed = await wait_for(2, lambda status: status["failureReason"] is not None)
                    assert failed["publishedVersion"] == 1
                    request = await client.post(base + "/search", headers=token, json=QUERY)
                    assert request.raise_for_status().json()["version"] == 1
            finally:
                await db.close()
            indexed = await wait_for(2, lambda status: status["publishedVersion"] == 2)
            assert indexed["attempt"] >= 2
            request = await client.post(base + "/ask", headers=token, json=QUERY)
            assert "60 seconds" in request.raise_for_status().json()["result"]["answer"]

    asyncio.run(verify())
