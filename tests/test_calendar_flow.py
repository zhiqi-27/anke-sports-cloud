from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from icalendar import Calendar
from sqlalchemy import select

from app.calendar import rebuild_feed
from app.db import Event, Job, Projection, User
from app.providers import sync_provider, upsert_event
from app.schemas import Config, ImportInput
from app.security import canonical_url
from app.service import attach_link, ensure_user, import_preview, save_config
from app.worker import run_one


def insert_event(sessions, days=2, suffix="a", **extra):
    start = datetime.now(timezone.utc) + timedelta(days=days)
    with sessions() as db:
        event = Event(
            source_key=f"test:{suffix}",
            competition_id="test:league",
            sport="basketball",
            title="测试比赛",
            starts_at=start.isoformat(),
            local_date=start.date().isoformat(),
            participants=[],
            provider="test",
            demo=True,
            **extra,
        )
        db.add(event)
        db.flush()
        user = db.get(User, "local-reviewer")
        overrides = [*user.config["event_overrides"], {"event_key": event.source_key, "state": "include"}]
        save_config(db, user, {**user.config, "event_overrides": overrides}, user.revision)
        db.commit()
        ident = event.id
    drain()
    return ident


def drain():
    for _ in range(30):
        if not run_one():
            return
    raise AssertionError("queue did not drain")


def feed_snapshot(client):
    address = client.get("/api/v1/me/feed/address").json()["url"]
    response = client.get(address)
    response.raise_for_status()
    events = [x for x in Calendar.from_ical(response.content).walk() if x.name == "VEVENT"]
    return address, response, events


def test_reschedule_content_and_rotation_keep_uid(stack):
    client, sessions = stack
    ident = insert_event(sessions)
    address, first, events = feed_snapshot(client)
    uid, seq = str(events[0]["UID"]), int(events[0]["SEQUENCE"])
    with sessions() as db:
        event = db.get(Event, ident)
        event.starts_at = (datetime.fromisoformat(event.starts_at) + timedelta(hours=2)).isoformat()
        rebuild_feed(db, "local-reviewer")
        db.commit()
    _, second, events = feed_snapshot(client)
    assert str(events[0]["UID"]) == uid
    assert int(events[0]["SEQUENCE"]) == seq + 1
    assert second.headers["etag"] != first.headers["etag"]
    payload = {"url": "https://youtu.be/abcdefghijk", "kind": "preview", "title": "手动链接测试"}
    added = client.post(f"/api/v1/events/{ident}/links", json=payload)
    assert added.status_code == 200
    duplicate = client.post(f"/api/v1/events/{ident}/links", json=payload)
    assert duplicate.json()["id"] == added.json()["id"]
    drain()
    _, third, events = feed_snapshot(client)
    assert str(events[0]["UID"]) == uid and int(events[0]["SEQUENCE"]) == seq + 2
    assert "abcdefghijk" in str(events[0]["DESCRIPTION"])
    client.post("/api/v1/me/feed/rotate", json={"confirmed": True}).raise_for_status()
    assert client.get(address).status_code == 404
    _, rotated, events = feed_snapshot(client)
    assert str(events[0]["UID"]) == uid and rotated.content == third.content


def test_identical_rebuild_and_conditional_get(stack):
    client, sessions = stack
    insert_event(sessions)
    address, first, _ = feed_snapshot(client)
    with sessions() as db:
        rebuild_feed(db, "local-reviewer")
        db.commit()
    _, second, _ = feed_snapshot(client)
    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]
    assert first.headers["last-modified"] == second.headers["last-modified"]
    assert client.get(address, headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    assert client.get(address, headers={"If-None-Match": "W/" + first.headers["etag"]}).status_code == 304
    assert client.head(address).content == b""


def test_link_block_survives_repeated_discovery_and_owner_isolation(stack):
    client, sessions = stack
    ident = insert_event(sessions)
    link = client.post(
        f"/api/v1/events/{ident}/links", json={"url": "https://youtu.be/abcdefghijk", "kind": "preview"}
    ).json()["id"]
    assert client.post(f"/api/v1/me/links/{link}/block").status_code == 200
    with sessions() as db:
        other = ensure_user(db, "another-user")
        secret = attach_link(
            db, other, db.get(Event, ident), "https://youtu.be/0123456789a", "private title", "recap"
        )
        other_id = secret.id
        db.commit()
    assert client.post(f"/api/v1/me/links/{other_id}/block").status_code == 404
    client.post(
        f"/api/v1/events/{ident}/links",
        json={"url": "https://www.youtube.com/watch?v=abcdefghijk", "kind": "preview"},
    )
    drain()
    assert client.get(f"/api/v1/events/{ident}").json()["links"] == []
    _, response, _ = feed_snapshot(client)
    assert "abcdefghijk" not in response.text and "private title" not in response.text


def test_revision_conflict_and_import_bound_to_preview(stack):
    client, sessions = stack
    before = client.get("/api/v1/me/calendar").json()
    prefs = {**before["config"]["preferences"], "timezone": "UTC"}
    body = {"expected_revision": before["revision"], "preferences": prefs}
    assert client.patch("/api/v1/me/preferences", json=body).status_code == 200
    assert client.patch("/api/v1/me/preferences", json=body).status_code == 409
    current = client.get("/api/v1/me/calendar").json()
    payload = {
        "config": current["config"],
        "mode": "replace",
        "dry_run": True,
        "expected_revision": current["revision"],
    }
    preview = client.post("/api/v1/me/config/import", json=payload).json()
    payload.update(dry_run=False, confirmation=preview["confirmation"])
    payload["config"]["preferences"]["timezone"] = "Asia/Tokyo"
    assert client.post("/api/v1/me/config/import", json=payload).status_code == 400
    exported = client.get("/api/v1/me/config/export").text
    assert "token" not in exported and "feed" not in exported


def test_merge_omitted_preferences_preserves_user_settings(stack):
    _, sessions = stack
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        config = Config().model_dump()
        config["preferences"]["timezone"] = "UTC"
        save_config(db, user, config, user.revision)
        merged, _ = import_preview(db, user, ImportInput(config=Config(), expected_revision=user.revision))
        assert merged["preferences"]["timezone"] == "UTC"


def test_unfollow_retains_past_but_explicit_exclusion_cancels(stack):
    client, sessions = stack
    past = insert_event(sessions, days=-1, suffix="past")
    future = insert_event(sessions, suffix="future")
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        save_config(db, user, {**user.config, "event_overrides": []}, user.revision)
        rebuild_feed(db, user.id)
        db.commit()
        projections = {p.event_id: p for p in db.scalars(select(Projection))}
        assert not projections[past].removed and projections[future].removed
    current = client.get("/api/v1/me/calendar").json()
    client.put(
        f"/api/v1/events/{past}/selection",
        json={"expected_revision": current["revision"], "state": "exclude"},
    ).raise_for_status()
    drain()
    _, _, events = feed_snapshot(client)
    assert all(str(e["STATUS"]) == "CANCELLED" for e in events)


def test_outbox_rolls_back_with_config(stack):
    _, sessions = stack
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        revision = user.revision
        jobs = len(db.scalars(select(Job)).all())
        save_config(db, user, user.config, revision)
        db.rollback()
        assert db.get(User, user.id).revision == revision
        assert len(db.scalars(select(Job)).all()) == jobs


def test_date_only_event_is_all_day_and_transparent(stack):
    client, sessions = stack
    ident = insert_event(sessions)
    with sessions() as db:
        event = db.get(Event, ident)
        event.starts_at = None
        event.time_precision = "date_only"
        rebuild_feed(db, "local-reviewer")
        db.commit()
    _, _, events = feed_snapshot(client)
    assert events[0]["DTSTART"].params["VALUE"] == "DATE"
    assert str(events[0]["TRANSP"]) == "TRANSPARENT"
    assert "[时间待定]" in str(events[0]["SUMMARY"])


def test_provider_partial_fetch_cannot_erase_schedule(stack, monkeypatch):
    _, sessions = stack
    ident = insert_event(sessions)
    monkeypatch.setattr(
        "app.providers.get_json", lambda *a, **kw: {"MRData": {"total": "2", "RaceTable": {"Races": []}}}
    )
    with sessions() as db:
        with pytest.raises(ValueError, match="INCOMPLETE_PAGINATION"):
            sync_provider(db, "jolpica")
        db.rollback()
        assert db.get(Event, ident) is not None


def test_upsert_keeps_identity(stack):
    _, sessions = stack
    ident = insert_event(sessions)
    with sessions() as db:
        event = db.get(Event, ident)
        upsert_event(db, event.source_key, title="时间更新后的测试比赛")
        db.commit()
        assert db.scalar(select(Event).where(Event.source_key == event.source_key)).id == ident


def test_auth_origin_and_local_host_boundary(stack):
    client, _ = stack
    assert (
        client.post("/api/v1/auth/local", headers={"Origin": "https://untrusted.example"}).status_code == 403
    )
    assert client.post("/api/v1/auth/local", headers={"Host": "untrusted.example"}).status_code == 404
    client.cookies.clear()
    assert client.get("/api/v1/me/calendar?userId=local-reviewer").status_code == 401
    assert client.get("/api/v1/me/feed/address").status_code == 401


@pytest.mark.parametrize(
    "url",
    [
        "http://youtube.com/watch?v=abcdefghijk",
        "https://127.0.0.1/foo",
        "https://youtube.com.evil.example/watch?v=abcdefghijk",
        "https://www.nba.com/",
        "https://www.nba.com/game/a?token=secret",
    ],
)
def test_unsafe_or_generic_links_rejected(url):
    with pytest.raises(HTTPException):
        canonical_url(url)
