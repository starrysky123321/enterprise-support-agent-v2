import os
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest


URLS = os.environ.get("KNOWLEDGE_ACCOUNT_URLS", "").split()
ADMIN = os.environ.get("KNOWLEDGE_API_TOKEN")
INDEX_CONTAINER = os.environ.get("KNOWLEDGE_LIFECYCLE_INDEX_CONTAINER")
pytestmark = pytest.mark.skipif(not URLS or not ADMIN, reason="Requires a dedicated Java knowledge API")
ROOT = "/api/knowledge"
PASSWORD = "Account-fixture-672"
NEW_PASSWORD = "Rotated-fixture-821"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_account_membership_and_revocation_across_api_replicas():
    first, last = URLS[0], URLS[-1]
    suffix = uuid4().hex[:12]
    email = f"review-{suffix}@example.com"
    workspace = f"account-{suffix}"
    route = f"{ROOT}/workspaces/{workspace}"
    other = f"{ROOT}/workspaces/private-{suffix}"
    credentials = {"email": email, "password": PASSWORD}
    admin = bearer(str(ADMIN))
    document = route + "/documents/guide"
    query = {"query": "What is the default idle timeout?"}

    with httpx.Client(timeout=10, trust_env=False) as client:
        def login(password: str) -> dict[str, str]:
            response = client.post(first + ROOT + "/auth/login", json={"email": email, "password": password})
            assert response.headers["Cache-Control"] == "no-store"
            return bearer(response.raise_for_status().json()["accessToken"])

        registered = client.post(first + ROOT + "/auth/register", json=credentials)
        assert registered.status_code == 201
        profile = registered.json()
        assert profile["email"] == email and profile["active"] and not profile["admin"]
        assert set(profile) == {"id", "email", "active", "admin", "verified", "department"}
        assert client.post(last + ROOT + "/auth/register",
                           json={**credentials, "email": email.upper()}).status_code == 409
        assert client.post(first + ROOT + "/auth/login",
                           json={**credentials, "password": "wrong"}).status_code == 401

        member, second_session = login(PASSWORD), login(PASSWORD)
        me = client.get(last + ROOT + "/users/me", headers=member).raise_for_status()
        assert me.json()["id"] == profile["id"]
        assert client.get(last + ROOT + "/users", headers=member).status_code == 403
        assert any(row["id"] == profile["id"] for row in client.get(
            first + ROOT + "/users", headers=admin, params={"limit": 100},
        ).raise_for_status().json()["items"])
        for path in (route, other):
            client.put(first + path, headers=admin).raise_for_status()
        membership = f"{route}/members/{profile['id']}"
        assert client.get(last + ROOT + "/workspaces", headers=member).raise_for_status().json() == []
        assert client.get(last + route + "/documents", headers=member).status_code == 403
        assert client.put(last + membership, headers=member, json={"role": "EDITOR"}).status_code == 403
        client.put(first + membership, headers=admin, json={"role": "READER"}).raise_for_status()
        assert client.get(last + ROOT + "/workspaces", headers=member).raise_for_status().json() == [
            {"workspaceId": workspace, "role": "READER"},
        ]
        assert client.get(last + route + "/documents", headers=member).status_code == 200
        assert client.post(last + document + "/versions", headers=member,
                           json={"text": "The default idle timeout is 30 seconds."}).status_code == 403
        assert client.post(last + other + "/search", headers=member, json=query).status_code == 403

        client.put(first + membership, headers=admin, json={"role": "EDITOR"}).raise_for_status()
        assert client.get(first + route + "/members", headers=admin).raise_for_status().json() == [
            {"accountId": profile["id"], "email": email, "role": "EDITOR"},
        ]
        client.post(last + document + "/versions", headers=member,
                    json={"text": "The default idle timeout is 30 seconds."}).raise_for_status()
        deadline = monotonic() + 30
        while monotonic() < deadline:
            row = client.get(last + document + "/versions/1", headers=member).raise_for_status().json()
            if row["status"] == "INDEXED":
                break
            assert row["status"] != "FAILED", row
            sleep(0.1)
        else:
            pytest.fail("The account's document was not published")
        assert client.post(last + route + "/search", headers=member, json=query).raise_for_status().json()["documents"]
        client.delete(first + membership, headers=admin).raise_for_status()
        assert client.post(last + route + "/search", headers=member, json=query).status_code == 403
        assert client.get(last + ROOT + "/users/me", headers=member).status_code == 200

        client.put(first + ROOT + "/users/me/password", headers=member,
                   json={"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD}).raise_for_status()
        for session in (member, second_session):
            assert client.get(last + ROOT + "/users/me", headers=session).status_code == 401
        assert client.post(last + ROOT + "/auth/login", json=credentials).status_code == 401
        renewed = login(NEW_PASSWORD)
        client.post(first + ROOT + "/auth/logout", headers=renewed).raise_for_status()
        assert client.get(last + ROOT + "/users/me", headers=renewed).status_code == 401

        renewed = login(NEW_PASSWORD)
        profile_path = f"{ROOT}/users/{profile['id']}"
        client.put(first + profile_path + "/department", headers=admin,
                   json={"department": "support"}).raise_for_status()
        assert client.get(last + ROOT + "/users/me", headers=renewed).raise_for_status().json()["department"] == "support"
        client.put(first + profile_path + "/status", headers=admin, json={"active": False}).raise_for_status()
        assert client.get(last + ROOT + "/users/me", headers=renewed).status_code == 401
        assert client.post(last + ROOT + "/auth/login",
                           json={**credentials, "password": NEW_PASSWORD}).status_code == 401


@pytest.mark.skipif(not INDEX_CONTAINER, reason="Requires the dedicated RAG container")
def test_rag_service_starts_without_user_modules_and_cannot_read_account_tables():
    probe = """
import asyncio
import os
import sys

import asyncpg

from src.api.knowledge_index import app

assert 'src.settings.config' not in sys.modules
assert 'src.modules.users.models' not in sys.modules
assert all(not route.path.startswith('/api/knowledge') for route in app.routes)

async def verify():
    db = await asyncpg.connect(os.environ['KNOWLEDGE_SEARCH_DATABASE_URL'])
    try:
        assert await db.fetchval('SELECT current_user') == 'knowledge_index'
        await db.fetchval('SELECT count(*) FROM knowledge_search.lexical_chunks')
        try:
            await db.fetchval('SELECT count(*) FROM knowledge_backend.accounts')
        except asyncpg.InsufficientPrivilegeError:
            return
        raise AssertionError('The RAG database role accessed the Java account catalog')
    finally:
        await db.close()

asyncio.run(verify())
"""
    subprocess.run(
        ["docker", "exec", str(INDEX_CONTAINER), "/app/.venv/bin/python", "-c", probe],
        check=True, timeout=20,
    )
