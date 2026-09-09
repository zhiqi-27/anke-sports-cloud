"""Exercise real SDK handlers with an isolated local owner; never mint live credentials."""

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db import OAuthGrant, OAuthRequest, OAuthTokenRecord, User
from app.oauth import clean_expired_connections, issuer, resource, revoke_connection, verify_access
from app.service import ensure_user

VERIFIER = "a" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
REDIRECT = "http://127.0.0.1:4567/callback"


def register(client, scopes="calendar:read calendar:write", redirect=REDIRECT):
    response = client.post(
        "/register",
        json={
            "client_name": "Isolated test client",
            "redirect_uris": [redirect],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": scopes,
        },
    )
    response.raise_for_status()
    return response.json()["client_id"]


def authorize(
    client, client_id, scopes="calendar:read calendar:write", audience=None, approved=True, redirect=REDIRECT
):
    response = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            "state": "test-state",
            "scope": scopes,
            "resource": audience or resource(),
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    pending = parse_qs(urlsplit(response.headers["location"]).query)["request"][0]
    preview = client.get("/api/v1/me/connections/requests/" + pending)
    assert preview.status_code == 200 and preview.json()["scopes"] == scopes.split()
    result = client.post(
        "/api/v1/me/connections/requests/" + pending, json={"approved": approved, "scopes": scopes.split()}
    )
    result.raise_for_status()
    query = parse_qs(urlsplit(result.json()["redirect_url"]).query)
    assert query["state"] == ["test-state"] and query["iss"] == [issuer()]
    return query, pending


def exchange(client, client_id, code, audience=None, **extra):
    return client.post(
        "/token",
        data={
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
            "resource": audience or resource(),
            **extra,
        },
    )


def test_pkce_single_use_resource_scope_and_no_plaintext_tokens(stack):
    client, sessions = stack
    cid = register(client)
    query, pending = authorize(client, cid)
    code = query["code"][0]
    assert exchange(client, cid, code, code_verifier="b" * 64).status_code == 400
    assert exchange(client, cid, code, audience=resource("extension")).status_code == 400
    assert exchange(client, cid, code, resource="").status_code == 400
    response = exchange(client, cid, code)
    assert response.status_code == 200
    tokens = response.json()
    assert tokens["expires_in"] == 900
    assert exchange(client, cid, code).status_code == 400
    assert (
        client.post(
            "/api/v1/me/connections/requests/" + pending, json={"approved": True, "scopes": ["calendar:read"]}
        ).status_code
        == 409
    )
    with sessions() as db:
        info = verify_access(db, tokens["access_token"], resource())
        assert info.subject == "local-reviewer" and info.claims["iss"] == issuer()
        assert verify_access(db, tokens["access_token"], resource("extension")) is None
        assert all(row.token_hash not in tokens.values() for row in db.scalars(select(OAuthTokenRecord)))
        assert db.scalar(select(OAuthRequest)).code_hash != code
    assert (
        client.get(
            "/api/v1/me/feed/address", headers={"Authorization": "Bearer " + tokens["access_token"]}
        ).status_code
        == 401
    )


def test_refresh_rotation_reuse_revokes_entire_grant(stack):
    client, sessions = stack
    cid = register(client)
    query, _ = authorize(client, cid)
    first = exchange(client, cid, query["code"][0]).json()
    data = {
        "client_id": cid,
        "grant_type": "refresh_token",
        "refresh_token": first["refresh_token"],
        "resource": resource(),
    }
    assert client.post("/token", data={**data, "scope": "feed:read"}).status_code == 400
    second = client.post("/token", data=data)
    assert second.status_code == 200
    assert second.json()["refresh_token"] != first["refresh_token"]
    assert client.post("/token", data=data).status_code == 400
    with sessions() as db:
        assert verify_access(db, first["access_token"], resource()) is None
        assert verify_access(db, second.json()["access_token"], resource()) is None


def test_extension_tokens_cannot_access_feed_or_delegate_authorization(stack):
    client, _ = stack
    cid = register(client, "calendar:read")
    query, _ = authorize(client, cid, "calendar:read", resource("extension"))
    token = exchange(client, cid, query["code"][0], resource("extension")).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/v1/me/calendar", headers=headers).status_code == 200
    assert client.get("/api/v1/me/feed/address", headers=headers).status_code == 403
    assert client.get("/api/v1/me/connections", headers=headers).status_code == 403
    assert (
        client.put(
            "/api/v1/me/follows", headers=headers, json={"expected_revision": 0, "follows": []}
        ).status_code
        == 403
    )
    assert (
        client.request("DELETE", "/api/v1/me", headers=headers, json={"confirmed": True}).status_code == 403
    )


def test_consent_cannot_add_unrequested_scope_and_denial_is_terminal(stack):
    client, _ = stack
    cid = register(client, "calendar:read")
    query, pending = authorize(client, cid, "calendar:read", approved=False)
    assert "code" not in query and query["error"] == ["access_denied"]
    assert client.get("/api/v1/me/connections/requests/" + pending).status_code == 409
    response = client.get(
        "/authorize",
        params={
            "client_id": cid,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": CHALLENGE,
            "scope": "calendar:read",
            "resource": resource(),
        },
        follow_redirects=False,
    )
    pending = parse_qs(urlsplit(response.headers["location"]).query)["request"][0]
    assert (
        client.post(
            "/api/v1/me/connections/requests/" + pending,
            json={"approved": True, "scopes": ["calendar:write"]},
        ).status_code
        == 400
    )


def test_revocation_owner_isolation_and_sdk_revocation(stack):
    client, sessions = stack
    cid = register(client)
    query, _ = authorize(client, cid)
    tokens = exchange(client, cid, query["code"][0]).json()
    connection = client.get("/api/v1/me/connections").json()["items"][0]
    with sessions() as db:
        other = ensure_user(db, "other-owner")
        with pytest.raises(HTTPException) as exc:
            revoke_connection(db, other, connection["id"])
        assert exc.value.status_code == 404
        db.rollback()
    assert (
        client.post("/revoke", data={"client_id": cid, "token": tokens["refresh_token"]}).status_code == 200
    )
    with sessions() as db:
        assert verify_access(db, tokens["access_token"], resource()) is None
    assert client.get("/api/v1/me/connections").json()["items"] == []


def test_issuer_expiry_metadata_and_redirect_validation(stack):
    client, sessions = stack
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert "none" in metadata["token_endpoint_auth_methods_supported"]
    assert metadata["authorization_response_iss_parameter_supported"]
    for bad in [
        "http://attacker.example/callback",
        "javascript:alert(1)",
        "https://example.test/callback?code=evil",
    ]:
        result = client.post(
            "/register",
            json={
                "redirect_uris": [bad],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        assert result.status_code == 400
    cid = register(client)
    query, _ = authorize(client, cid)
    tokens = exchange(client, cid, query["code"][0]).json()
    with sessions() as db:
        grant = db.scalar(select(OAuthGrant))
        grant.issuer = "https://other.example/"
        db.commit()
        assert verify_access(db, tokens["access_token"], resource()) is None
        grant.issuer = issuer()
        grant.expires_at = int(time.time()) - 1
        db.commit()
        assert verify_access(db, tokens["access_token"], resource()) is None


def test_owner_deletion_and_expired_record_cleanup(stack):
    client, sessions = stack
    cid = register(client)
    query, _ = authorize(client, cid)
    tokens = exchange(client, cid, query["code"][0]).json()
    with sessions() as db:
        row = db.scalar(select(OAuthRequest))
        row.expires_at = int(time.time()) - 1
        row.code_expires_at = int(time.time()) + 10
        db.commit()
    clean_expired_connections()
    with sessions() as db:
        assert db.scalar(select(OAuthRequest)) is not None
        assert verify_access(db, tokens["access_token"], resource()) is not None
    result = client.request("DELETE", "/api/v1/me", json={"confirmed": True})
    assert result.status_code == 200
    with sessions() as db:
        assert db.get(User, "local-reviewer").deleted
        assert db.scalar(select(OAuthGrant)) is None
        assert db.scalar(select(OAuthTokenRecord)) is None
        assert db.scalar(select(OAuthRequest)) is None
        assert verify_access(db, tokens["access_token"], resource()) is None


def test_chrome_origin_is_bound_to_registered_callback_and_owner(stack):
    from tests.test_calendar_flow import insert_event

    client, sessions = stack
    ident = insert_event(sessions)
    extension_id = "a" * 32
    redirect = f"https://{extension_id}.chromiumapp.org/callback"
    cid = register(client, redirect=redirect)
    query, _ = authorize(client, cid, audience=resource("extension"), redirect=redirect)
    response = exchange(client, cid, query["code"][0], resource("extension"), redirect_uri=redirect)
    response.raise_for_status()
    bearer = "Bearer " + response.json()["access_token"]
    headers = {
        "Origin": f"chrome-extension://{extension_id}",
        "Authorization": bearer,
        "Idempotency-Key": "chrome-fixture-save",
    }
    payload = {
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "title": "隔离扩展合成测试",
        "kind": "preview",
    }
    path = f"/api/v1/events/{ident}/links"
    wrong = client.post(path, headers={**headers, "Origin": "chrome-extension://" + "b" * 32}, json=payload)
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "ORIGIN_REJECTED"
    client.cookies.clear()  # Extension fetch uses credentials: omit; only bearer owns the write.
    first = client.post(path, headers=headers, json=payload)
    first.raise_for_status()
    repeated = client.post(path, headers=headers, json=payload)
    assert repeated.json() == first.json()
    assert first.json()["event"]["included"] is True
    assert first.json()["id"] in [link["id"] for link in first.json()["event"]["links"]]
    block_path = f"/api/v1/me/links/{first.json()['id']}/block"
    assert client.post(block_path, headers=headers).status_code == 409
    blocked = client.post(block_path, headers={**headers, "Idempotency-Key": "chrome-fixture-block"})
    blocked.raise_for_status()
    readded = client.post(path, headers={**headers, "Idempotency-Key": "chrome-fixture-readd"}, json=payload)
    readded.raise_for_status()
    assert readded.json()["event"]["links"] == []
    assert client.get("/api/v1/me/feed/address", headers=headers).status_code == 403
    client.post(
        "/revoke", data={"client_id": cid, "token": response.json()["refresh_token"]}
    ).raise_for_status()
    assert client.get("/api/v1/me/calendar", headers=headers).status_code == 401


def test_web_callback_client_cannot_claim_a_chrome_origin(stack):
    client, _ = stack
    cid = register(client)
    query, _ = authorize(client, cid, audience=resource("extension"))
    token = exchange(client, cid, query["code"][0], resource("extension")).json()["access_token"]
    response = client.put(
        "/api/v1/me/follows",
        headers={"Origin": "chrome-extension://" + "a" * 32, "Authorization": "Bearer " + token},
        json={"expected_revision": 0, "follows": []},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "ORIGIN_REJECTED"
