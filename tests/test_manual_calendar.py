from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from icalendar import Calendar
import pytest
from sqlalchemy import select

from app.config import settings
from app.db import Event, Job, Source
from app.document_api import create_app
from app.document_accounts import owner_partition
from app.document_runtime import Runtime
from app.document_store import LocalDocumentStore, partition_items
from tests.test_calendar_flow import drain as drain_sql
from tests.test_document_runtime import drain as drain_documents, ics_rows, samples


@pytest.fixture
def manual_document_stack(tmp_path):
    store = LocalDocumentStore(tmp_path / "runtime.db")
    cfg = settings().model_copy(
        update={"storage_backend": "documents-local", "document_local_path": str(store.path)}
    )
    runtime = Runtime(store, cfg)
    rows, sources = samples()
    runtime.catalog.publish("fixture", rows, sources, expected_revision=0, complete=True)
    app = create_app(store, cfg)
    with TestClient(app, headers={"Origin": cfg.web_url}) as client:
        yield client, runtime, rows, sources


def add_sql_event(sessions, ident, *, participants=None):
    start = datetime.now(timezone.utc) + timedelta(days=3)
    with sessions() as db:
        db.add(
            Event(
                id=ident,
                source_key=f"test:event:{ident}",
                competition_id="test:league",
                sport="basketball",
                title=f"手动比赛 {ident}",
                starts_at=start.isoformat(),
                local_date=start.date().isoformat(),
                provider="test",
                participants=[
                    {
                        "id": item["id"],
                        "name": item.get("name", item["id"]),
                        "short_name": item.get("short_name", item["id"]),
                        "color": item.get("color", "#123456"),
                    }
                    for item in (participants or [])
                ],
                demo=True,
            )
        )
        db.commit()
    return ident


def change_body(client):
    return {"expected_revision": client.get("/api/v1/me/calendar").json()["revision"]}


def test_sql_manual_calendar_lifecycle_is_atomic_and_uid_stable(stack):
    client, sessions = stack
    ident = add_sql_event(sessions, "manual-sql")
    body = change_body(client)
    added = client.post(
        f"/api/v1/me/calendar/events/{ident}",
        json=body,
        headers={"Idempotency-Key": "manual-add-sql"},
    )
    assert added.status_code == 200, added.text
    first = added.json()
    assert first["config"]["manual_events"] == [{"event_id": ident}]
    assert first["revision"] == body["expected_revision"] + 1
    stale = client.post(
        f"/api/v1/me/calendar/events/{ident}",
        json=body,
        headers={"Idempotency-Key": "manual-add-stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
    with sessions() as db:
        pending = db.scalar(
            select(Job.id).where(
                Job.kind == "projection",
                Job.state == "pending",
                Job.payload["user_id"].as_string() == "local-reviewer",
            )
        )
        assert pending
    membership = client.get(f"/api/v1/events/{ident}").json()["calendar"]
    assert membership == {
        "sources": [{"type": "manual", "key": ident, "name": "手动添加"}],
        "can_remove": True,
    }
    drain_sql()
    address = client.get("/api/v1/me/feed/address").json()["url"]
    first_feed = client.get(address)
    first_uid = str(next(row for row in Calendar.from_ical(first_feed.content).walk("VEVENT"))["UID"])

    replay = client.post(
        f"/api/v1/me/calendar/events/{ident}",
        json=body,
        headers={"Idempotency-Key": "manual-add-sql"},
    )
    assert replay.status_code == 200 and replay.json() == first
    duplicate = client.post(
        f"/api/v1/me/calendar/events/{ident}",
        json=change_body(client),
        headers={"Idempotency-Key": "manual-add-sql-2"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["revision"] == first["revision"]

    removed = client.request(
        "DELETE",
        f"/api/v1/me/calendar/events/{ident}",
        json=change_body(client),
        headers={"Idempotency-Key": "manual-remove-sql"},
    )
    assert removed.status_code == 200
    assert removed.json()["config"]["manual_events"] == []
    drain_sql()
    assert client.get(address).status_code == 200
    assert not Calendar.from_ical(client.get(address).content).walk("VEVENT")

    readded = client.post(
        f"/api/v1/me/calendar/events/{ident}",
        json=change_body(client),
        headers={"Idempotency-Key": "manual-readd-sql"},
    )
    assert readded.status_code == 200
    drain_sql()
    second_feed = client.get(address)
    second_uid = str(next(row for row in Calendar.from_ical(second_feed.content).walk("VEVENT"))["UID"])
    assert second_uid == first_uid


def test_sql_overlap_unfollow_and_import_cannot_bypass_manual_delete(stack):
    client, sessions = stack
    with sessions() as db:
        for source_id, name in (
            ("test:manual-team", "手动球队"),
            ("test:manual-team-b", "手动球队 B"),
        ):
            db.add(
                Source(
                    id=source_id,
                    name=name,
                    short_name=name,
                    sport="basketball",
                    kind="team",
                    color="#123456",
                    provider="test",
                    demo=True,
                )
            )
        db.commit()
    ident = add_sql_event(
        sessions,
        "manual-overlap",
        participants=[{"id": "test:manual-team"}, {"id": "test:manual-team-b"}],
    )
    added = client.post(
        f"/api/v1/me/calendar/events/{ident}",
        json=change_body(client),
        headers={"Idempotency-Key": "overlap-add"},
    )
    assert added.status_code == 200
    drain_sql()
    exported = client.get("/api/v1/me/config/export").json()
    round_trip = client.post(
        "/api/v1/me/config/import",
        json={
            "config": exported,
            "mode": "replace",
            "dry_run": True,
            "expected_revision": added.json()["revision"],
        },
    )
    assert round_trip.status_code == 200, round_trip.text
    assert round_trip.json()["unresolved"] == []
    exported["manual_events"] = []
    bypass = client.post(
        "/api/v1/me/config/import",
        json={
            "config": exported,
            "mode": "replace",
            "dry_run": True,
            "expected_revision": added.json()["revision"],
        },
    )
    assert bypass.status_code == 409
    assert bypass.json()["error"]["code"] == "MANUAL_EVENTS_IMPORT_FORBIDDEN"
    assert client.get("/api/v1/me/calendar").json()["config"]["manual_events"] == [{"event_id": ident}]

    current = client.get("/api/v1/me/calendar").json()
    follows = {
        "expected_revision": current["revision"],
        "follows": [
            {"type": "team", "source_key": "test:manual-team"},
            {"type": "team", "source_key": "test:manual-team-b"},
        ],
    }
    assert client.put("/api/v1/me/follows", json=follows).status_code == 200
    drain_sql()
    membership = client.get(f"/api/v1/events/{ident}").json()["calendar"]
    assert {source["type"] for source in membership["sources"]} == {"manual", "follow"}
    assert {source["key"] for source in membership["sources"] if source["type"] == "follow"} == {
        "test:manual-team",
        "test:manual-team-b",
    }
    assert membership["can_remove"] is False
    refused = client.request(
        "DELETE",
        f"/api/v1/me/calendar/events/{ident}",
        json=change_body(client),
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "EVENT_MANAGED_BY_FOLLOW"

    current = client.get("/api/v1/me/calendar").json()
    assert client.put(
        "/api/v1/me/follows",
        json={
            "expected_revision": current["revision"],
            "follows": [{"type": "team", "source_key": "test:manual-team-b"}],
        },
    ).status_code == 200
    drain_sql()
    membership = client.get(f"/api/v1/events/{ident}").json()["calendar"]
    assert {source["key"] for source in membership["sources"] if source["type"] == "follow"} == {
        "test:manual-team-b"
    }
    assert membership["can_remove"] is False
    current = client.get("/api/v1/me/calendar").json()
    assert client.put(
        "/api/v1/me/follows",
        json={"expected_revision": current["revision"], "follows": []},
    ).status_code == 200
    drain_sql()
    assert client.get(f"/api/v1/events/{ident}").json()["calendar"]["can_remove"] is True
    removed = client.request(
        "DELETE",
        f"/api/v1/me/calendar/events/{ident}",
        json=change_body(client),
        headers={"Idempotency-Key": "overlap-remove"},
    )
    assert removed.status_code == 200


def test_document_manual_calendar_lifecycle_and_overlap(manual_document_stack):
    client, runtime, rows, _ = manual_document_stack
    client.post("/api/v1/auth/local").raise_for_status()
    drain_documents(runtime)
    ident = rows[0]["id"]
    body = change_body(client)
    added = client.post(
        f"/api/v1/me/calendar/events/{ident}",
        json=body,
        headers={"Idempotency-Key": "manual-add-document"},
    )
    assert added.status_code == 200, added.text
    assert added.json()["config"]["manual_events"] == [{"event_id": ident}]
    pk = owner_partition("local-reviewer")
    assert any(
        row["state"] == "pending" and row["payload"]["operation"] == "projection"
        for row in partition_items(runtime.store, "state", pk, "outbox")
    )
    drain_documents(runtime)
    address = client.get("/api/v1/me/feed/address").json()["url"]
    first_uid = next(iter(ics_rows(client.get(address))))
    replay = client.post(
        f"/api/v1/me/calendar/events/{ident}",
        json=body,
        headers={"Idempotency-Key": "manual-add-document"},
    )
    assert replay.status_code == 200 and replay.json() == added.json()
    duplicate = client.post(
        f"/api/v1/me/calendar/events/{ident}",
        json=change_body(client),
        headers={"Idempotency-Key": "manual-add-document-2"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["revision"] == added.json()["revision"]
    exported = client.get("/api/v1/me/config/export").json()
    round_trip = client.post(
        "/api/v1/me/config/import",
        json={
            "config": exported,
            "mode": "replace",
            "dry_run": True,
            "expected_revision": duplicate.json()["revision"],
        },
    )
    assert round_trip.status_code == 200, round_trip.text
    assert round_trip.json()["unresolved"] == []

    current = client.get("/api/v1/me/calendar").json()
    follow_payload = {
        "expected_revision": current["revision"],
        "follows": [{"type": "team", "source_key": "fixture:team"}],
    }
    assert client.put("/api/v1/me/follows", json=follow_payload).status_code == 200
    drain_documents(runtime)
    membership = client.get(f"/api/v1/events/{ident}").json()["calendar"]
    assert {source["type"] for source in membership["sources"]} == {"manual", "follow"}
    refused = client.request(
        "DELETE",
        f"/api/v1/me/calendar/events/{ident}",
        json=change_body(client),
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "EVENT_MANAGED_BY_FOLLOW"
    current = client.get("/api/v1/me/calendar").json()
    assert client.put(
        "/api/v1/me/follows",
        json={"expected_revision": current["revision"], "follows": []},
    ).status_code == 200
    drain_documents(runtime)
    assert client.get(f"/api/v1/events/{ident}").json()["calendar"]["can_remove"] is True
    removed = client.request(
        "DELETE",
        f"/api/v1/me/calendar/events/{ident}",
        json=change_body(client),
        headers={"Idempotency-Key": "manual-remove-document"},
    )
    assert removed.status_code == 200
    drain_documents(runtime)
    assert ics_rows(client.get(address)) == {}
    readded = client.post(
        f"/api/v1/me/calendar/events/{ident}",
        json=change_body(client),
        headers={"Idempotency-Key": "manual-readd-document"},
    )
    assert readded.status_code == 200
    drain_documents(runtime)
    assert next(iter(ics_rows(client.get(address)))) == first_uid


@pytest.mark.parametrize("path", ["/api/v1/me/calendar/events/missing"])
def test_manual_calendar_requires_authentication(stack, path):
    client, _ = stack
    client.cookies.clear()
    response = client.post(path, json={"expected_revision": 0})
    assert response.status_code == 401
