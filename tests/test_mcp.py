"""Official SDK client -> Streamable HTTP -> service -> isolated SQL database."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select
from starlette.applications import Starlette

from app.db import CommandReceipt, Event, OAuthGrant, OAuthTokenRecord, User
from app.mcp_server import build_mcp
from app.oauth import resource
from app.seed import seed_demo
from app.security import digest
from app.service import ensure_user
from test_oauth import authorize, exchange, register


@asynccontextmanager
async def connected(token=None, public=False):
    routes, lifespan = build_mcp()
    app = Starlette(routes=routes)
    headers = {"Authorization": "Bearer " + token} if token else {}
    async with lifespan(), httpx.AsyncClient(transport=httpx.ASGITransport(app), headers=headers) as http:
        async with streamable_http_client(
            "http://testserver/mcp" + ("/public" if public else ""), http_client=http
        ) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=5)) as session:
                await session.initialize()
                yield session


def grant(client, scopes="calendar:read calendar:write", audience=None):
    cid = register(client, scopes)
    query, _ = authorize(client, cid, scopes, audience)
    return exchange(client, cid, query["code"][0], audience).json()["access_token"]


def fixtures(sessions):
    with sessions() as db:
        seed_demo(db)
        db.commit()
        event = db.scalar(select(Event).where(Event.demo.is_(True)))
        return event.id, "demo:lakers"


def test_public_mcp_and_http_auth_boundary(stack):
    client, sessions = stack
    event_id, _ = fixtures(sessions)
    metadata = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert metadata["resource"] == resource() and metadata["scopes_supported"] == ["calendar:read"]
    missing = client.post("/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})
    assert missing.status_code == 401 and "resource_metadata=" in missing.headers["www-authenticate"]
    token = grant(client, audience=resource("extension"))
    assert client.post("/mcp", headers={"Authorization": "Bearer " + token}, json={}).status_code == 401
    assert client.post("/mcp?access_token=" + token, json={}).status_code == 401

    async def run():
        async with connected(public=True) as session:
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {"search_sources", "get_schedule", "get_event"}
            assert all(t.annotations.readOnlyHint for t in tools.tools)
            event = await session.call_tool("get_event", {"event_id": event_id})
            assert not event.isError and event.structuredContent["demo"]
            assert not event.structuredContent["links"]
            invalid = await session.call_tool("get_event", {"event_id": event_id, "userId": "other"})
            assert invalid.isError
            sources = await session.call_tool("search_sources", {"dataset": "demo", "limit": 1})
            assert (
                len(sources.structuredContent["items"]) == 1 and sources.structuredContent["next_offset"] == 1
            )

    asyncio.run(run())


def test_private_mcp_shared_writes_receipts_blocks_and_import(stack):
    client, sessions = stack
    event_id, source_key = fixtures(sessions)
    token = grant(client)

    async def run():
        async with connected(token) as session:
            names = {t.name for t in (await session.list_tools()).tools}
            assert names == {
                "search_sources",
                "get_schedule",
                "get_event",
                "get_my_calendar",
                "update_follows",
                "attach_event_link",
                "remove_event_link",
                "export_config",
                "import_config",
                "get_calendar_feed",
            }
            calendar = (await session.call_tool("get_my_calendar")).structuredContent
            args = {
                "add": [{"type": "team", "source_key": source_key}],
                "remove": [],
                "expected_revision": calendar["revision"],
                "idempotency_key": "follow-command-01",
            }
            first = await session.call_tool("update_follows", args)
            assert not first.isError and first.structuredContent["revision"] == calendar["revision"] + 1
            assert (
                await session.call_tool("update_follows", args)
            ).structuredContent == first.structuredContent
            assert (await session.call_tool("update_follows", {**args, "remove": [source_key]})).isError
            denied = await session.call_tool("get_calendar_feed")
            assert denied.isError and "feed:read" in denied.content[0].text
            data = {
                "url": "https://www.nba.com/game/local-fixture",
                "title": "合成测试链接",
                "kind": "live",
            }
            attached = await session.call_tool(
                "attach_event_link",
                {"event_id": event_id, "data": data, "idempotency_key": "attach-command-01"},
            )
            assert not attached.isError
            link_id = attached.structuredContent["id"]
            # Same command and key through HTTP returns the exact MCP result.
            replay = client.post(
                f"/api/v1/events/{event_id}/links",
                json=data,
                headers={"Idempotency-Key": "attach-command-01"},
            )
            assert replay.status_code == 200 and replay.json() == attached.structuredContent
            assert (
                await session.call_tool(
                    "remove_event_link", {"link_id": link_id, "idempotency_key": "remove-command-01"}
                )
            ).structuredContent["blocked"]
            again = await session.call_tool(
                "attach_event_link",
                {"event_id": event_id, "data": data, "idempotency_key": "attach-command-02"},
            )
            assert not again.structuredContent["event"]["links"]
            config = (await session.call_tool("export_config")).structuredContent
            assert "user_id" not in config and "feed" not in config and "token" not in str(config)
            current = (await session.call_tool("get_my_calendar")).structuredContent
            draft = {
                "config": config,
                "mode": "replace",
                "expected_revision": current["revision"],
                "dry_run": True,
            }
            preview = await session.call_tool(
                "import_config", {"data": draft, "idempotency_key": "import-command-01"}
            )
            assert not preview.isError and not preview.structuredContent["applied"]
            assert (
                await session.call_tool(
                    "import_config",
                    {"data": {**draft, "dry_run": False}, "idempotency_key": "import-command-02"},
                )
            ).isError
            applied = await session.call_tool(
                "import_config",
                {
                    "data": {
                        **draft,
                        "dry_run": False,
                        "confirmation": preview.structuredContent["confirmation"],
                    },
                    "idempotency_key": "import-command-02",
                },
            )
            assert not applied.isError and applied.structuredContent["applied"]

    asyncio.run(run())
    with sessions() as db:
        assert len(list(db.scalars(select(CommandReceipt)))) == 5


def test_mcp_scope_revocation_owner_and_redacted_failure(stack, monkeypatch, caplog):
    client, sessions = stack
    event_id, _ = fixtures(sessions)
    read_token = grant(client, "calendar:read")

    async def read_only():
        async with connected(read_token) as session:
            result = await session.call_tool(
                "remove_event_link", {"link_id": "someone-elses-link", "idempotency_key": "readonly-command"}
            )
            assert result.isError and "calendar:write" in result.content[0].text

    asyncio.run(read_only())
    full = grant(client, "calendar:read calendar:write feed:read")

    async def can_read_feed():
        async with connected(full) as session:
            result = await session.call_tool("get_calendar_feed")
            assert not result.isError and "/feeds/" in result.structuredContent["url"]
            from app import actions

            def fail(*args):
                raise RuntimeError("DO_NOT_LOG_PRIVATE_MARKER")

            monkeypatch.setattr(actions, "add_link", fail)
            result = await session.call_tool(
                "attach_event_link",
                {
                    "event_id": event_id,
                    "data": {"url": "https://www.nba.com/game/failed-fixture", "kind": "live"},
                    "idempotency_key": "failed-command-01",
                },
            )
            assert result.isError and "DO_NOT_LOG_PRIVATE_MARKER" not in result.content[0].text

    asyncio.run(can_read_feed())
    assert "DO_NOT_LOG_PRIVATE_MARKER" not in caplog.text
    with sessions() as db:
        assert db.scalar(select(CommandReceipt)) is None
        for row in db.scalars(select(OAuthGrant)):
            row.revoked = True
        db.commit()
    assert client.post("/mcp", headers={"Authorization": "Bearer " + full}, json={}).status_code == 401


def test_shared_schedule_range_pagination_and_cursor_binding(stack):
    client, sessions = stack
    fixtures(sessions)
    with sessions() as db:
        event = db.scalar(select(Event).where(Event.demo.is_(True)))
        start = event.starts_at[:10]
    params = {
        "from": start + "T00:00:00Z",
        "to": (datetime.fromisoformat(start) + timedelta(days=10)).isoformat() + "Z",
        "dataset": "demo",
        "limit": 1,
    }
    first = client.get("/api/v1/events", params=params).json()
    assert first["next_cursor"]
    second = client.get("/api/v1/events", params={**params, "cursor": first["next_cursor"]}).json()
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert (
        client.get(
            "/api/v1/events", params={**params, "cursor": first["next_cursor"], "q": "changed"}
        ).status_code
        == 409
    )
    assert (
        client.get(
            "/api/v1/events", params={**params, "from": "2026-01-01T00:00:00Z", "to": "2026-07-01T00:00:00Z"}
        ).status_code
        == 400
    )
    assert client.get("/api/v1/events", params={**params, "cursor": "bad"}).status_code == 409


def test_mcp_actor_cannot_read_or_remove_another_owners_link(stack):
    client, sessions = stack
    event_id, source_key = fixtures(sessions)
    link = client.post(
        f"/api/v1/events/{event_id}/links",
        json={
            "url": "https://www.nba.com/game/mcp-private",
            "title": "仅原用户可见",
            "kind": "live",
        },
    ).json()
    other_token = grant(client)
    with sessions() as db:
        other = ensure_user(db, "isolated-second-owner")
        record = db.get(OAuthTokenRecord, digest(other_token))
        db.get(OAuthGrant, record.grant_id).owner_id = other.id
        original_config = db.get(User, "local-reviewer").config
        db.commit()

    async def run():
        async with connected(other_token) as session:
            assert (await session.call_tool("get_my_calendar")).structuredContent[
                "id"
            ] == "isolated-second-owner"
            assert not (await session.call_tool("get_event", {"event_id": event_id})).structuredContent[
                "links"
            ]
            result = await session.call_tool(
                "remove_event_link", {"link_id": link["id"], "idempotency_key": "other-owner-delete"}
            )
            assert result.isError and "NOT_FOUND" in result.content[0].text
            result = await session.call_tool(
                "update_follows",
                {
                    "add": [{"type": "team", "source_key": source_key}],
                    "remove": [],
                    "expected_revision": 0,
                    "idempotency_key": "other-owner-follow",
                },
            )
            assert not result.isError and result.structuredContent["id"] == "isolated-second-owner"

    asyncio.run(run())
    with sessions() as db:
        assert db.get(User, "local-reviewer").config == original_config
