from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest


URLS = os.environ.get("KNOWLEDGE_REDIS_TEST_URLS", "").split()
TOKEN = os.environ.get("KNOWLEDGE_API_TOKEN")
REDIS = os.environ.get("KNOWLEDGE_REDIS_TEST_CONTAINER")
QUERY = {"query": "What is the default idle timeout?"}

pytestmark = pytest.mark.skipif(
    len(URLS) != 2 or not TOKEN or not REDIS,
    reason="Requires two API instances and a dedicated Redis test container",
)


def test_quota_is_shared_across_replicas_and_isolated_by_workspace_and_operation():
    with httpx.Client(trust_env=False, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        def seed_workspace() -> str:
            workspace = f"quota-{uuid4().hex[:12]}"
            path = f"/api/knowledge/workspaces/{workspace}"
            client.put(URLS[0] + path).raise_for_status()
            doc = path + "/documents/timeout"
            client.post(URLS[0] + doc + "/versions", json={
                "text": "The default idle timeout is 30 seconds.",
            }).raise_for_status()
            deadline = monotonic() + 30
            while monotonic() < deadline:
                version = client.get(URLS[1] + doc + "/versions/1").raise_for_status().json()
                if version["publishedVersion"] == 1:
                    return doc
                assert version["status"] != "FAILED", version
                sleep(0.1)
            pytest.fail("Seed document was not published")

        def request(doc: str, operation: str, replica: int = 0) -> httpx.Response:
            return client.post(URLS[replica] + doc + f"/{operation}", json=QUERY)

        first, other = seed_workspace(), seed_workspace()
        with ThreadPoolExecutor(max_workers=10) as pool:
            responses = list(pool.map(lambda i: request(first, "search", i % 2), range(10)))
        assert Counter(item.status_code for item in responses) == {200: 3, 429: 7}
        assert all(1 <= int(item.headers["Retry-After"]) <= 4 for item in responses if item.status_code == 429)
        assert request(other, "search", 1).status_code == 200

        assert request(first, "ask", 0).status_code == 200
        assert request(first, "ask", 1).status_code == 200
        rejected = request(first, "ask", 0)
        assert rejected.status_code == 429
        sleep(int(rejected.headers["Retry-After"]) + 0.05)
        assert request(first, "ask", 1).status_code == 200

        subprocess.run(["docker", "stop", "--time", "10", REDIS], check=True, timeout=20, capture_output=True)
        try:
            assert request(first, "search", 0).status_code == 200
            assert request(first, "search", 1).status_code == 200
            for replica in (0, 1):
                answer = request(first, "ask", replica)
                assert answer.status_code == 503
                assert answer.headers["Retry-After"] == "1"
        finally:
            subprocess.run(["docker", "start", REDIS], check=True, timeout=20, capture_output=True)

        deadline = monotonic() + 15
        while monotonic() < deadline:
            if request(other, "ask", 1).status_code == 200:
                return
            sleep(0.25)
        pytest.fail("Question answering did not recover after Redis restarted")
