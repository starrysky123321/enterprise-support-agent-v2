import asyncio
import os
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import asyncpg
import httpx
import pytest


URL = os.getenv("KNOWLEDGE_LIFECYCLE_URL")
TOKEN = os.getenv("KNOWLEDGE_API_TOKEN")
INDEX_CONTAINER = os.getenv("KNOWLEDGE_LIFECYCLE_INDEX_CONTAINER")
CATALOG_DB_URL = os.getenv("KNOWLEDGE_CLUSTER_DB_URL")
pytestmark = pytest.mark.skipif(not URL or not TOKEN, reason="Requires a dedicated knowledge API")


def make_pdf(*pages: str) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Count {len(pages)} /Kids [{' '.join(f'{4 + i * 2} 0 R' for i in range(len(pages)))}] >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
            b"<< /Font << /F1 3 0 R >> >> /Contents " + f"{5 + i * 2} 0 R >>".encode(),
        )
        stream = f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode()
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Root 1 0 R /Size {len(offsets)} >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(pdf)


def indexed(client: httpx.Client, path: str, version: int, headers: dict[str, str]) -> None:
    deadline = monotonic() + 45
    while monotonic() < deadline:
        status = client.get(f"{path}/versions/{version}", headers=headers).raise_for_status().json()
        if status["publishedVersion"] == version:
            return
        assert status["status"] != "FAILED", status
        sleep(0.1)
    pytest.fail(f"Version {version} of {path} was not published")


def test_pdf_pagination_workspace_citations_and_delete():
    suffix = uuid4().hex[:12]
    workspace = f"/api/knowledge/workspaces/lifecycle-{suffix}"
    other = f"/api/knowledge/workspaces/private-{suffix}"
    primary_doc = workspace + "/documents/guides"
    secondary_doc = workspace + "/documents/retries"
    admin = {"Authorization": f"Bearer {TOKEN}"}
    pdf = make_pdf("Nebula Gateway operates regional edge connections.",
                   "The default idle timeout for Nebula Gateway is 30 seconds.")
    query = {"query": "What is the default idle timeout? What is the maximum retry count?"}

    with httpx.Client(base_url=URL, timeout=12, trust_env=False) as client:
        for path in (workspace, other):
            client.put(path, headers=admin).raise_for_status()

        def key(role: str) -> dict[str, str]:
            result = client.post(workspace + "/keys", headers=admin, json={"role": role}).raise_for_status().json()
            return {"Authorization": f"Bearer {result['token']}"}

        editor, reader = key("EDITOR"), key("READER")
        empty = client.post(workspace + "/search", headers=reader, json=query).raise_for_status().json()
        assert empty["result"]["chunks"] == [] and empty["documents"] == []
        first = client.post(primary_doc + "/versions/pdf", headers=editor,
                            files={"file": ("gateway.pdf", pdf, "application/pdf")}).raise_for_status().json()
        duplicate = client.post(primary_doc + "/versions/pdf", headers=editor,
                                files={"file": ("another-name.pdf", pdf, "application/pdf")})
        assert duplicate.raise_for_status().json()["versionNumber"] == first["versionNumber"] == 1
        assert client.post(primary_doc + "/versions/pdf", headers=editor,
                           files={"file": ("bad.pdf", b"bad", "application/pdf")}).status_code == 415
        client.post(secondary_doc + "/versions", headers=editor,
                    json={"text": "The maximum retry count for Nebula Gateway is 4 attempts."}).raise_for_status()
        private_doc = other + "/documents/guides"
        client.post(private_doc + "/versions", headers=admin,
                    json={"text": "The default idle timeout is 777 seconds."}).raise_for_status()
        for path, credentials in ((primary_doc, editor), (secondary_doc, editor), (private_doc, admin)):
            indexed(client, path, 1, credentials)

        listing = client.get(workspace + "/documents", headers=reader, params={"limit": 1}).raise_for_status().json()
        assert [doc["documentId"] for doc in listing["items"]] == ["guides"]
        assert listing["items"][0]["filename"] == "gateway.pdf"
        tail = client.get(workspace + "/documents", headers=reader,
                          params={"after": listing["nextCursor"], "limit": 1}).raise_for_status().json()
        assert [doc["documentId"] for doc in tail["items"]] == ["retries"]
        assert tail["nextCursor"] is None
        assert client.get(other + "/documents", headers=reader).status_code == 403
        assert client.post(other + "/search", headers=reader, json=query).status_code == 403
        assert client.delete(primary_doc, headers=reader).status_code == 403

        hits = client.post(workspace + "/search", headers=reader, json=query).raise_for_status().json()
        assert {doc["documentId"] for doc in hits["documents"]} == {"guides", "retries"}
        assert any("30 seconds" in chunk["text"] and chunk["page_number"] == 2 for chunk in hits["result"]["chunks"])
        assert all("777 seconds" not in chunk["text"] for chunk in hits["result"]["chunks"])
        reply = client.post(workspace + "/ask", headers=reader, json=query).raise_for_status().json()
        assert "30 seconds" in reply["result"]["answer"]
        assert "4 attempts" in reply["result"]["answer"]
        assert {doc["documentId"] for doc in reply["documents"]} == {"guides", "retries"}
        assert any(citation["page_number"] == 2 for citation in reply["result"]["citations"])

        client.post(primary_doc + "/versions", headers=editor,
                    json={"text": "A superseding PDF version awaiting publication."}).raise_for_status()
        assert client.delete(primary_doc, headers=editor).status_code == 202
        assert client.delete(primary_doc, headers=editor).status_code == 202
        assert client.get(primary_doc + "/versions/1", headers=reader).status_code == 404
        assert client.get(primary_doc + "/versions/2", headers=reader).status_code == 404
        assert client.post(primary_doc + "/versions/2/retry", headers=editor).status_code == 404
        hits = client.post(workspace + "/search", headers=reader, json=query).raise_for_status().json()
        assert {doc["documentId"] for doc in hits["documents"]} == {"retries"}
        resubmit = client.post(primary_doc + "/versions/pdf", headers=editor,
                               files={"file": ("gateway.pdf", pdf, "application/pdf")})
        assert resubmit.raise_for_status().json()["versionNumber"] == 3
        indexed(client, primary_doc, 3, editor)
        assert client.get(primary_doc + "/versions/1", headers=reader).status_code == 404


def test_unparseable_pdf_fails_once_and_keeps_the_published_version():
    path = f"/api/knowledge/documents/pdf-retry-{uuid4().hex[:12]}"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    filename = "网关手册.pdf"
    pdf = make_pdf("The default idle timeout for Nebula Gateway is 42 seconds.")
    query = {"query": "What is the default idle timeout?"}

    with httpx.Client(base_url=URL, timeout=12, trust_env=False) as client:
        client.post(path + "/versions/pdf", headers=headers,
                    files={"file": (filename, pdf, "application/pdf")}).raise_for_status()
        indexed(client, path, 1, headers)

        damaged = client.post(path + "/versions/pdf", headers=headers,
                              files={"file": ("truncated.pdf", b"%PDF-1.7\ntruncated", "application/pdf")})
        assert damaged.status_code == 202
        assert damaged.json()["versionNumber"] == 2
        deadline = monotonic() + 20
        while monotonic() < deadline:
            version = client.get(path + "/versions/2", headers=headers).raise_for_status().json()
            if version["status"] == "FAILED":
                break
            assert version["status"] != "INDEXED", version
            sleep(0.1)
        else:
            pytest.fail("The damaged PDF did not reach a terminal failure")
        assert version["attempt"] == 1 and version["publishedVersion"] == 1, version
        assert "422" in version["failureReason"], version

        result = client.post(path + "/search", headers=headers, json=query).raise_for_status().json()
        assert result["version"] == 1
        assert result["result"]["chunks"][0]["source"] == filename
        assert "42 seconds" in result["result"]["chunks"][0]["text"]


@pytest.mark.skipif(not INDEX_CONTAINER or not CATALOG_DB_URL, reason="Requires the test catalog DSN and index container")
def test_retired_payload_and_indexes_are_purged_and_late_index_writes_are_rejected():
    doc_id = f"purge-{uuid4().hex[:12]}"
    route = f"/api/knowledge/documents/{doc_id}"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with httpx.Client(base_url=URL, timeout=12, trust_env=False) as client:
        client.post(route + "/versions", headers=headers,
                    json={"text": "The default idle timeout is 30 seconds."}).raise_for_status()
        indexed(client, route, 1, headers)
        client.delete(route, headers=headers).raise_for_status()

    async def retired_version(dsn: str) -> str:
        db = await asyncpg.connect(dsn)
        try:
            async with asyncio.timeout(15):
                while True:
                    row = await db.fetchrow("""
                        SELECT v.id, v.purged_at IS NOT NULL AND v.body IS NULL AND v.pdf IS NULL AS payload_purged,
                               EXISTS (SELECT FROM knowledge_search.retired_versions WHERE doc_id = v.id::text) AS retired,
                               EXISTS (SELECT FROM knowledge_search.dense_chunks WHERE doc_id = v.id::text) AS dense,
                               EXISTS (SELECT FROM knowledge_search.lexical_chunks WHERE doc_id = v.id::text) AS lexical
                        FROM knowledge_backend.document_versions v
                        WHERE v.workspace_id = 'default' AND v.document_id = $1
                    """, doc_id)
                    if row["payload_purged"]:
                        assert row["retired"] and not row["dense"] and not row["lexical"], row
                        return str(row["id"])
                    await asyncio.sleep(0.1)
        finally:
            await db.close()

    version_id = asyncio.run(retired_version(str(CATALOG_DB_URL)))
    probe = """
import asyncio
import os
import sys

import httpx

async def verify():
    token = os.environ["KNOWLEDGE_INDEX_TOKEN"]
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8011",
                                 headers={"Authorization": f"Bearer {token}"}) as client:
        late = await client.post("/internal/index", json={
            "indexId": sys.argv[1], "source": sys.argv[2], "text": "Stale retry."
        })
        assert late.status_code == 410, late.text

asyncio.run(verify())
"""
    subprocess.run(
        ["docker", "exec", str(INDEX_CONTAINER), "/app/.venv/bin/python", "-c", probe, version_id, doc_id],
        check=True, timeout=25,
    )
