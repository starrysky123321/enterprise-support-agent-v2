from contextlib import closing
import os
from pathlib import Path
import sqlite3
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest


@pytest.mark.skipif(
    not all(os.environ.get(name) for name in (
        "KNOWLEDGE_E2E_URL", "KNOWLEDGE_API_TOKEN", "KNOWLEDGE_INDEX_DIR",
    )),
    reason="Requires the dedicated Spring Boot and Python publication test services",
)
def test_search_and_ask_follow_published_version_after_partial_index_failure():
    base_url = os.environ["KNOWLEDGE_E2E_URL"]
    token = os.environ["KNOWLEDGE_API_TOKEN"]
    keyword_db = Path(os.environ["KNOWLEDGE_INDEX_DIR"]) / "bm25.db"
    assert keyword_db.is_file()
    document = f"publication-smoke-{uuid4().hex}"
    route = f"/api/knowledge/documents/{document}"
    query = {"query": "What is the default idle timeout?"}

    with httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"}, trust_env=False) as client:
        def submit(seconds: int) -> dict:
            response = client.post(f"{route}/versions", json={
                "text": f"The default idle timeout for Nebula Gateway is {seconds} seconds.",
            })
            return response.raise_for_status().json()

        def wait_for_status(version: int, expected: str) -> dict:
            deadline = monotonic() + 20
            while True:
                result = client.get(f"{route}/versions/{version}").raise_for_status().json()
                if result["status"] == expected:
                    return result
                if result["status"] in {"INDEXED", "FAILED"} or monotonic() >= deadline:
                    pytest.fail(f"Expected version {version} to be {expected}, got {result}")
                sleep(0.1)

        def assert_read_version(version: int, seconds: int) -> None:
            search = client.post(f"{route}/search", json=query).raise_for_status().json()
            assert search["version"] == version
            assert search["result"]["chunks"]
            assert all(f"{seconds} seconds" in chunk["text"] for chunk in search["result"]["chunks"])
            answer = client.post(f"{route}/ask", json=query).raise_for_status().json()
            assert answer["version"] == version
            assert answer["result"]["status"] == "ok", answer
            assert f"{seconds} seconds" in answer["result"]["answer"]
            citations = answer["result"]["citations"]
            assert citations and all(f"{seconds} seconds" in c["snippet"] for c in citations)

        assert client.get(f"{route}/versions/1", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.post(f"{route}/search", json=query).status_code == 409
        assert submit(30)["versionNumber"] == 1
        assert wait_for_status(1, "INDEXED")["publishedVersion"] == 1
        assert submit(30)["attempt"] == 1
        assert_read_version(1, 30)

        # The vector write runs first. Hold only the SQLite write lock, so the
        # second index rejects writes while the published version stays readable.
        with closing(sqlite3.connect(keyword_db)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            assert submit(60)["versionNumber"] == 2
            assert_read_version(1, 30)
            failed = wait_for_status(2, "FAILED")
            assert failed["publishedVersion"] == 1
            assert failed["attempt"] == 1
            assert failed["failureReason"]

        assert_read_version(1, 30)
        assert client.post(f"{route}/versions/2/retry").status_code == 202
        indexed = wait_for_status(2, "INDEXED")
        assert indexed["publishedVersion"] == 2
        assert indexed["attempt"] == 2
        assert_read_version(2, 60)

        # An older version can finish after a newer version has been published.
        with closing(sqlite3.connect(keyword_db)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            assert submit(75)["versionNumber"] == 3
            assert wait_for_status(3, "FAILED")["publishedVersion"] == 2
        assert submit(90)["versionNumber"] == 4
        assert wait_for_status(4, "INDEXED")["publishedVersion"] == 4
        assert client.post(f"{route}/versions/3/retry").status_code == 202
        assert wait_for_status(3, "INDEXED")["publishedVersion"] == 4
        assert_read_version(4, 90)
