from tests.test_document_runtime import document_stack as document_stack
from app.oauth import resource
from tests.test_oauth import authorize, exchange, register


def test_document_sdk_pkce_rotation_scope_revoke_and_delete(document_stack):
    client, rt, _, _ = document_stack
    client.post("/api/v1/auth/local")
    cid = register(client)
    query, _ = authorize(client, cid, audience=resource("extension"))
    code = query["code"][0]
    assert (
        exchange(client, cid, code, code_verifier="b" * 64, audience=resource("extension")).status_code == 400
    )
    first = exchange(client, cid, code, audience=resource("extension"))
    assert first.status_code == 200, first.text
    assert exchange(client, cid, code, audience=resource("extension")).status_code == 400
    token = first.json()
    headers = {"Authorization": "Bearer " + token["access_token"]}
    assert client.get("/api/v1/me/calendar", headers=headers).status_code == 200
    assert client.get("/api/v1/me/feed/address", headers=headers).status_code == 403
    assert (
        client.request("DELETE", "/api/v1/me", json={"confirmed": True}, headers=headers).status_code == 403
    )
    data = {
        "client_id": cid,
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
        "resource": resource("extension"),
    }
    second = client.post("/token", data=data)
    assert second.status_code == 200, second.text
    assert client.post("/token", data=data).status_code == 400
    assert (
        client.get(
            "/api/v1/me/calendar", headers={"Authorization": "Bearer " + second.json()["access_token"]}
        ).status_code
        == 401
    )
    query, _ = authorize(client, cid, audience=resource("extension"))
    fresh = exchange(client, cid, query["code"][0], audience=resource("extension")).json()
    assert rt.oauth.verify_access(fresh["access_token"])
    rt.privacy.delete("local-reviewer")
    assert rt.oauth.verify_access(fresh["access_token"]) is None


def test_extension_origin_is_bound_to_registered_callback(document_stack):
    client, rt, _, _ = document_stack
    client.post("/api/v1/auth/local")
    ext = "a" * 32
    cid = register(client, redirect="https://" + ext + ".chromiumapp.org/callback")
    query, _ = authorize(
        client, cid, audience=resource("extension"), redirect="https://" + ext + ".chromiumapp.org/callback"
    )
    response = exchange(
        client,
        cid,
        query["code"][0],
        audience=resource("extension"),
        redirect_uri="https://" + ext + ".chromiumapp.org/callback",
    )
    assert response.status_code == 200, response.text
    headers = {
        "Authorization": "Bearer " + response.json()["access_token"],
        "Origin": "chrome-extension://" + ext,
    }
    # Reaches authorized business validation; never accepts another extension origin.
    assert client.patch("/api/v1/me/preferences", json={}, headers=headers).status_code == 422
    headers["Origin"] = "chrome-extension://" + "b" * 32
    assert client.patch("/api/v1/me/preferences", json={}, headers=headers).status_code == 403


def test_grant_ownership_revocation_and_expiry(document_stack):
    from app.document_accounts import owner_partition
    from app.document_store import Write, clean
    from fastapi import HTTPException
    import pytest
    import time

    client, rt, _, _ = document_stack
    client.post("/api/v1/auth/local")
    cid = register(client)
    query, _ = authorize(client, cid, audience=resource("extension"))
    tokens = exchange(client, cid, query["code"][0], audience=resource("extension")).json()
    grant_id = rt.oauth.verify_access(tokens["access_token"]).claims["grant_id"]
    rt.accounts.ensure("other-owner")
    with pytest.raises(HTTPException) as failure:
        rt.oauth.disconnect("other-owner", grant_id)
    assert failure.value.status_code == 404
    assert rt.oauth.verify_access(tokens["access_token"])
    pk = owner_partition("local-reviewer")
    grant = rt.store.get("state", pk, "grant:" + grant_id)
    expired = clean(grant)
    expired["payload"]["expires_at"] = int(time.time()) - 1
    rt.store.batch("state", pk, [Write("replace", grant["id"], expired, grant["_etag"])])
    assert rt.oauth.verify_access(tokens["access_token"]) is None
