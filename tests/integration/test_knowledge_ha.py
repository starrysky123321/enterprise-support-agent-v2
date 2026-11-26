from collections import Counter
from contextlib import contextmanager
import os
import re
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest


URL = os.getenv("KNOWLEDGE_HA_TEST_URL")
PROJECT = os.getenv("KNOWLEDGE_HA_TEST_PROJECT")
TOKEN = os.getenv("KNOWLEDGE_API_TOKEN")
pytestmark = pytest.mark.skipif(not URL or not PROJECT or not TOKEN, reason="Requires a disposable HA Compose project")
QUERY = {"query": "What is the default idle timeout?"}


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, text=True, capture_output=True, timeout=30).stdout.strip()


def container(service: str) -> str:
    ids = docker(
        "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}",
        "--filter", f"label=com.docker.compose.service={service}",
    ).splitlines()
    assert len(ids) == 1, (service, ids)
    return ids[0]


def execute(service: str, *args: str) -> str:
    return docker("exec", container(service), *args)


@contextmanager
def stopped(service: str):
    node = container(service)
    start = monotonic()
    docker("stop", "--time", "0", node)
    try:
        yield start
    finally:
        docker("start", node)


def wait_for(condition, seconds: float = 60):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        result = condition()
        if result:
            return result
        sleep(0.25)
    pytest.fail(f"{condition.__name__} did not succeed within {seconds} seconds")


def postgres_primary() -> str | None:
    for node in ("pg-1", "pg-2", "pg-3"):
        status = subprocess.run([
            "docker", "exec", container(node), "python3", "-c",
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8008/primary',timeout=1).close()",
        ], capture_output=True, timeout=3)
        if status.returncode == 0:
            return node
    return None


def kafka_topic(broker: str) -> str:
    return execute(broker, "/opt/kafka/bin/kafka-topics.sh", "--bootstrap-server", f"{broker}:9092",
                   "--describe", "--topic", "knowledge.index.requested.v1")


def redis_master() -> str:
    master, _ = execute("redis-sentinel-1", "redis-cli", "-p", "26379", "sentinel",
                        "get-master-addr-by-name", "knowledge-redis").splitlines()
    for service in ("redis", "redis-replica-1", "redis-replica-2"):
        ip = docker("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container(service))
        if ip == master:
            return service
    return ""


def test_acknowledged_data_and_traffic_survive_single_node_faults():
    route = f"/api/knowledge/documents/ha-{uuid4().hex[:12]}"
    with httpx.Client(base_url=URL, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=10, trust_env=False) as client:
        def submit(timeout: int) -> int | None:
            response = client.post(route + "/versions", json={"text": f"The default idle timeout is {timeout} seconds."})
            return response.json()["versionNumber"] if response.status_code == 202 else None

        def published(version: int):
            response = client.get(f"{route}/versions/{version}")
            if response.status_code == 200:
                status = response.json()
                assert status["status"] != "FAILED", status
                return status["publishedVersion"] == version
            return False

        def search(version: int):
            response = client.post(route + "/search", json=QUERY)
            return response.status_code == 200 and response.json()["version"] == version

        assert submit(30) == 1
        wait_for(lambda: published(1))
        original = wait_for(postgres_primary)

        def synchronous_standby():
            return execute(original, "psql", "-U", "knowledge", "-d", "knowledge", "-Atc",
                           "SELECT application_name FROM pg_stat_replication WHERE sync_state = 'sync'")

        wait_for(synchronous_standby)
        with stopped(original) as start:
            wait_for(lambda: search(1))
            read_s = monotonic() - start
            promoted = postgres_primary()
            assert promoted and promoted != original
            assert wait_for(lambda: submit(31)) == 2
            wait_for(lambda: published(2))
            print(f"PostgreSQL {original} -> {promoted}: existing search {read_s:.2f}s, new publication {monotonic() - start:.2f}s")

        with stopped("postgres") as start:
            wait_for(lambda: search(2))
            assert wait_for(lambda: submit(32)) == 3
            wait_for(lambda: published(3))
            print(f"PostgreSQL proxy fallback: new publication {monotonic() - start:.2f}s")

        topic = kafka_topic("kafka-2")
        assert "ReplicationFactor: 3" in topic and "min.insync.replicas=2" in topic
        leaders = set(re.findall(r"Leader:\s+([123])\b", topic))
        assert leaders
        brokers = {"1": "kafka", "2": "kafka-2", "3": "kafka-3"}
        leader = min(leaders)
        survivor = next(service for number, service in brokers.items() if number != leader)
        with stopped(brokers[leader]) as start:
            assert wait_for(lambda: submit(33)) == 4
            wait_for(lambda: published(4))
            reelected = kafka_topic(survivor)
            partitions = re.findall(r"Leader:\s+(\d+)\s+Replicas:.*?Isr:\s+([\d,]+)", reelected)
            assert len(partitions) == 12
            assert all(node != leader and len(set(isr.split(","))) >= 2 for node, isr in partitions)
            print(f"Kafka broker {leader} down: 12 partitions available, new publication {monotonic() - start:.2f}s")

        def answer_version_four():
            response = client.post(route + "/ask", json=QUERY)
            ask_status[str(response.status_code)] += 1
            return response.status_code == 200 and response.json()["version"] == 4

        ask_status: Counter[str] = Counter()
        wait_for(answer_version_four)
        ask_status.clear()
        original_redis = redis_master()
        assert original_redis in {"redis", "redis-replica-1", "redis-replica-2"}
        with stopped(original_redis) as start:
            assert search(4)
            wait_for(answer_version_four)
            promoted_redis = wait_for(lambda: (name if (name := redis_master()) and name != original_redis else None))
            print(f"Redis {original_redis} -> {promoted_redis}: ask resumed in {monotonic() - start:.2f}s, statuses {dict(ask_status)}")
