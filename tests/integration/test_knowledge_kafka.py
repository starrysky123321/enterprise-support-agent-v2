from collections.abc import Callable
from contextlib import contextmanager
import os
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest


URL = os.environ.get("KNOWLEDGE_KAFKA_TEST_URL")
TOKEN = os.environ.get("KNOWLEDGE_API_TOKEN")
BROKER = os.environ.get("KNOWLEDGE_KAFKA_TEST_BROKER")
POSTGRES = os.environ.get("KNOWLEDGE_KAFKA_TEST_POSTGRES")
TOPIC = "knowledge.index.requested.v1"

pytestmark = pytest.mark.skipif(
    not all((URL, TOKEN, BROKER, POSTGRES)),
    reason="Requires the dedicated Kafka and PostgreSQL Compose test containers",
)


def docker(*args: str, body: str | None = None, check: bool = True, timeout: int = 30):
    return subprocess.run(
        ["docker", *args], input=body, text=True, check=check, timeout=timeout,
        capture_output=True,
    )


def query_database(sql: str) -> str:
    return docker("exec", POSTGRES, "psql", "-At", "-v", "ON_ERROR_STOP=1",
                  "-U", "knowledge", "-d", "knowledge", "-c", sql).stdout.strip()


@contextmanager
def block_lexical_writes():
    with subprocess.Popen(
        ["docker", "exec", "-i", POSTGRES, "psql", "-Atq", "-v", "ON_ERROR_STOP=1",
         "-U", "knowledge", "-d", "knowledge"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    ) as session:
        assert session.stdin is not None and session.stdout is not None
        session.stdin.write("SET lock_timeout = '3s'; BEGIN; "
                            "LOCK TABLE knowledge_search.lexical_chunks IN SHARE MODE; SELECT 'locked';\n")
        session.stdin.flush()
        try:
            assert session.stdout.readline().strip() == "locked"
            yield
        finally:
            session.stdin.close()
            session.wait(timeout=5)


def test_broker_outage_recovery_duplicate_delivery_and_dead_letter():
    doc = f"kafka-smoke-{uuid4().hex[:12]}"
    route = f"/api/knowledge/documents/{doc}"
    query = {"query": "What is the default idle timeout?"}
    with httpx.Client(base_url=URL, trust_env=False, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        def upload(seconds: int) -> dict:
            return client.post(route + "/versions", json={
                "text": f"The default idle timeout is {seconds} seconds.",
            }).raise_for_status().json()

        def version(number: int) -> dict:
            return client.get(f"{route}/versions/{number}").raise_for_status().json()

        def wait_for_version(number: int, condition: Callable[[dict], bool]) -> dict:
            deadline = monotonic() + 90
            while monotonic() < deadline:
                status = version(number)
                if condition(status):
                    return status
                assert status["status"] != "FAILED", status
                sleep(0.25)
            pytest.fail(f"Version {number} did not reach the expected state within 90 seconds")

        def wait_for_publication(number: int) -> dict:
            return wait_for_version(number, lambda status: status["publishedVersion"] == number)

        assert upload(30)["versionNumber"] == 1
        assert wait_for_publication(1)["attempt"] == 1
        docker("stop", "--time", "10", BROKER)
        try:
            assert upload(60)["versionNumber"] == 2
            assert version(2)["status"] == "PENDING"
            assert query_database(f"""
                SELECT count(*) FROM knowledge_backend.index_outbox o
                JOIN knowledge_backend.document_versions v ON v.id = o.job_id
                WHERE v.workspace_id = 'default' AND v.document_id = '{doc}' AND v.version_number = 2
            """) == "1"
            assert client.post(route + "/search", json=query).raise_for_status().json()["version"] == 1
            assert "30 seconds" in client.post(route + "/ask", json=query).raise_for_status().json()["result"]["answer"]
        finally:
            docker("start", BROKER)

        assert wait_for_publication(2)["attempt"] == 1
        assert "60 seconds" in client.post(route + "/ask", json=query).raise_for_status().json()["result"]["answer"]
        job_id = query_database(f"""
            SELECT id FROM knowledge_backend.document_versions
            WHERE workspace_id = 'default' AND document_id = '{doc}' AND version_number = 2
        """)
        invalid = f"invalid-{uuid4().hex}"
        # The same key places the duplicate and sentinel on one partition. The DLT
        # confirms the worker has advanced beyond both records in partition order.
        docker("exec", "-i", BROKER, "/opt/kafka/bin/kafka-console-producer.sh",
               "--bootstrap-server", "kafka:9092", "--topic", TOPIC,
               "--property", "parse.key=true", "--property", "key.separator=:",
               body=f"{job_id}:{job_id}\n{job_id}:{invalid}\n")
        dead_letters = docker(
            "exec", BROKER, "/opt/kafka/bin/kafka-console-consumer.sh",
            "--bootstrap-server", "kafka:9092", "--topic", f"{TOPIC}.DLT",
            "--from-beginning", "--timeout-ms", "7000", check=False,
        ).stdout.splitlines()
        assert invalid in dead_letters
        assert version(2)["attempt"] == 1

        with block_lexical_writes():
            assert upload(90)["versionNumber"] == 3
            delayed = wait_for_version(3, lambda status: status["failureReason"] is not None)
            assert delayed["publishedVersion"] == 2
            assert client.post(route + "/search", json=query).raise_for_status().json()["version"] == 2
        assert wait_for_publication(3)["attempt"] >= 2
        assert "90 seconds" in client.post(route + "/ask", json=query).raise_for_status().json()["result"]["answer"]
