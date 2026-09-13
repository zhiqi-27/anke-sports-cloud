from tests.test_document_runtime import document_stack as document_stack
from fastapi import HTTPException
import pytest
from app.document_accounts import owner_partition
from app.document_store import partition_items
from tests.test_document_runtime import drain, follow


def test_erasure_revokes_before_worker_and_preserves_other_owner(document_stack):
    client, rt, _, _ = document_stack
    client.post("/api/v1/auth/local")
    follow(client)
    drain(rt)
    address = client.get("/api/v1/me/feed/address").json()["url"]
    rt.accounts.ensure("another-owner")
    assert client.request("DELETE", "/api/v1/me", json={"confirmed": False}).status_code == 400
    response = client.request("DELETE", "/api/v1/me", json={"confirmed": True})
    assert response.status_code == 200, response.text
    assert client.get(address).status_code == 404
    assert client.post("/api/v1/auth/local").status_code == 403
    with pytest.raises(HTTPException):
        rt.accounts.ensure("local-reviewer")
    drain(rt)
    remaining = list(partition_items(rt.store, "state", owner_partition("local-reviewer"), None))
    assert {r["kind"] for r in remaining} == {"account", "feed", "outbox"}
    assert len([r for r in remaining if r["kind"] == "outbox"]) == 1
    assert rt.accounts.active("another-owner")["payload"]["deleted"] is False
