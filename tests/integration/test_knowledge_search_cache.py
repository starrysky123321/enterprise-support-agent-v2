import os
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest


URLS = os.environ.get("KNOWLEDGE_CACHE_TEST_URLS", "").split()
TOKEN = os.environ.get("KNOWLEDGE_API_TOKEN")
REDIS = os.environ.get("KNOWLEDGE_CACHE_TEST_REDIS_CONTAINER")
CACHE_LIMIT = os.environ.get("KNOWLEDGE_CACHE_TEST_MAX_ENTRIES")
pytestmark = pytest.mark.skipif(len(URLS) < 2 or not TOKEN, reason="Requires two dedicated knowledge API replicas")
ROOT = "/api/knowledge/workspaces"
QUERY = {"query": "What is the default idle timeout?"}
EXPIRATIONS = "knowledge:cache:{search}:v1:expirations"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def redis_cli(*command: str) -> str:
    return subprocess.check_output(["docker", "exec", str(REDIS), "redis-cli", "--raw", *command],
                                   timeout=10, text=True).strip()


def create_workspace(client: httpx.Client, name: str) -> tuple[str, dict[str, str]]:
    path = f"{ROOT}/{name}-{uuid4().hex[:12]}"
    admin = bearer(str(TOKEN))
    client.put(URLS[0] + path, headers=admin).raise_for_status()
    key = client.post(URLS[0] + path + "/keys", headers=admin, json={"role": "READER"}).raise_for_status().json()
    return path, bearer(key["token"])


def publish(client: httpx.Client, document: str, seconds: int, version: int) -> None:
    admin = bearer(str(TOKEN))
    path = URLS[0] + document + "/versions"
    payload = {"text": f"The default idle timeout is {seconds} seconds."}
    response = client.post(path, headers=admin, json=payload).raise_for_status().json()
    assert response["versionNumber"] == version
    deadline = monotonic() + 30
    while monotonic() < deadline:
        current = client.get(f"{path}/{version}", headers=admin).raise_for_status().json()
        if current["publishedVersion"] == version:
            return
        assert current["status"] != "FAILED", current
        sleep(0.1)
    pytest.fail(f"Version {version} of {document} was not published")


def metric(client: httpx.Client, url: str, outcome: str) -> int:
    result = client.get(url + "/actuator/prometheus", headers=bearer(str(TOKEN))).raise_for_status().text
    for line in result.splitlines():
        if line.startswith("knowledge_search_cache_total{") and f'outcome="{outcome}"' in line:
            return int(float(line.split()[-1]))
    return 0


def test_search_cache_is_shared_and_respects_workspace_and_publication_changes():
    first, second = URLS[:2]
    with httpx.Client(timeout=12, trust_env=False) as client:
        path, reader = create_workspace(client, "cache")
        other, other_reader = create_workspace(client, "private")
        document = path + "/documents/gateway"
        publish(client, document, 30, 1)
        publish(client, other + "/documents/gateway", 777, 1)

        cold = metric(client, first, "miss")
        hits = metric(client, second, "hit")
        response = client.post(first + path + "/search", headers=reader, json=QUERY).raise_for_status().json()
        assert "30 seconds" in response["result"]["chunks"][0]["text"]
        assert metric(client, first, "miss") == cold + 1
        warm = client.post(second + path + "/search", headers=reader, json=QUERY).raise_for_status().json()
        assert warm == response
        assert metric(client, second, "hit") == hits + 1
        single = client.post(second + document + "/search", headers=reader, json=QUERY).raise_for_status().json()
        assert single["result"] == response["result"]
        assert metric(client, second, "hit") == hits + 2

        separate = client.post(second + other + "/search", headers=other_reader, json=QUERY).raise_for_status().json()
        assert "777 seconds" in separate["result"]["chunks"][0]["text"]
        assert client.post(second + other + "/search", headers=reader, json=QUERY).status_code == 403
        assert client.post(second + path + "/search", headers=other_reader, json=QUERY).status_code == 403

        publish(client, document, 60, 2)
        updated = client.post(second + path + "/search", headers=reader, json=QUERY).raise_for_status().json()
        assert updated["documents"][0]["version"] == 2
        assert "60 seconds" in updated["result"]["chunks"][0]["text"]
        client.delete(first + document, headers=bearer(str(TOKEN))).raise_for_status()
        removed = client.post(second + path + "/search", headers=reader, json=QUERY).raise_for_status().json()
        assert removed["result"]["chunks"] == []
        assert client.post(second + document + "/search", headers=reader, json=QUERY).status_code == 409


@pytest.mark.skipif(not REDIS or not CACHE_LIMIT, reason="Requires the test Redis container and configured cache bound")
def test_cache_bound_does_not_evict_quota_keys_and_redis_outage_falls_back():
    url = URLS[0]
    with httpx.Client(timeout=12, trust_env=False) as client:
        path, reader = create_workspace(client, "cache-cap")
        publish(client, path + "/documents/gateway", 41, 1)

        for i in range(int(str(CACHE_LIMIT)) + 3):
            request = {"query": QUERY["query"] + f" Check {i}."}
            client.post(url + path + "/search", headers=reader, json=request).raise_for_status()
        keys = redis_cli("ZRANGE", EXPIRATIONS, "0", "-1").splitlines()
        assert len(keys) == int(str(CACHE_LIMIT))
        assert all(0 < int(redis_cli("PTTL", key)) <= 300_000 for key in keys)
        quota_key = f"knowledge:quota:{path.rsplit('/', 1)[-1]}:search"
        assert redis_cli("EXISTS", quota_key) == "1"

        previous = metric(client, url, "unavailable")
        subprocess.run(["docker", "stop", "--time", "0", str(REDIS)], check=True, timeout=15)
        try:
            result = client.post(url + path + "/search", headers=reader, json=QUERY).raise_for_status().json()
            assert "41 seconds" in result["result"]["chunks"][0]["text"]
            assert metric(client, url, "unavailable") > previous
        finally:
            subprocess.run(["docker", "start", str(REDIS)], check=True, timeout=15)
