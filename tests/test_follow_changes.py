from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from pydantic import ValidationError

from app.calendar import event_is_past, rebuild_feed
from app.db import Base, Event, Feed, Projection, Source, User
from app.schemas import Config
from app.service import ensure_user, save_config
from tests.test_calendar_flow import drain, feed_snapshot


def follow(key, kind="team"):
    return {"type": kind, "source_key": f"test:{key}"}


def setup_schedule(sessions):
    with sessions() as db:
        for key, kind in [
            ("a", "team"),
            ("b", "team"),
            ("c", "team"),
            ("league", "competition"),
        ]:
            db.add(
                Source(
                    id=f"test:{key}",
                    name=key,
                    short_name=key,
                    sport="basketball",
                    kind=kind,
                    color="#123456",
                    provider="test",
                    demo=True,
                )
            )
        instant = datetime.now(timezone.utc)
        ids = {}
        for key, days, teams in [
            ("a", 2, ["a"]),
            ("both", 3, ["a", "b"]),
            ("b", 4, ["b"]),
            ("past", -2, ["a"]),
            ("pinned", 5, ["a"]),
        ]:
            start = instant + timedelta(days=days)
            event = Event(
                source_key=f"test:event:{key}",
                competition_id="test:league",
                sport="basketball",
                title=f"合成比赛 {key}",
                starts_at=start.isoformat(),
                local_date=start.date().isoformat(),
                provider="test",
                demo=True,
                participants=[{"id": f"test:{team}"} for team in teams],
            )
            db.add(event)
            db.flush()
            ids[key] = event.id
        user = db.get(User, "local-reviewer")
        save_config(
            db,
            user,
            {
                **user.config,
                "follows": [follow("a"), follow("b")],
                "manual_events": [{"event_id": ids["pinned"]}],
            },
            user.revision,
        )
        db.commit()
    drain()
    return ids


def payload(client, follows):
    return {"expected_revision": client.get("/api/v1/me/calendar").json()["revision"], "follows": follows}


def snapshot(sessions):
    with sessions() as db:
        return {table.name: db.execute(select(table)).all() for table in Base.metadata.sorted_tables}


def test_preview_is_read_only_and_matches_published_removal_with_overlap_and_history(stack):
    client, sessions = stack
    ids = setup_schedule(sessions)
    body = payload(client, [follow("b")])
    original = snapshot(sessions)
    result = client.post("/api/v1/me/follows/preview", json=body)
    assert result.status_code == 200
    preview = result.json()
    assert snapshot(sessions) == original
    assert preview["removed"]["total"] == preview["removed"]["future"] == 1
    assert preview["removed"]["items"][0]["id"] == ids["a"]
    assert {e["id"] for e in preview["retained"]["items"]} == {ids["both"], ids["pinned"]}
    assert preview["historical_retained"] == 1
    assert preview["result_count"] == 4
    assert preview["added"]["total"] == 0
    _, _, old_events = feed_snapshot(client)
    old_uids = {str(e["UID"]) for e in old_events}
    headers = {"Idempotency-Key": "follow-removal-review"}
    saved = client.put(
        "/api/v1/me/follows", json={**body, "confirmation": preview["confirmation"]}, headers=headers
    )
    assert saved.status_code == 200
    drain()
    # An identical retry after publication must replay the receipt, not fail preview/revision.
    retry = client.put(
        "/api/v1/me/follows", json={**body, "confirmation": preview["confirmation"]}, headers=headers
    )
    assert retry.json() == saved.json()
    _, _, events = feed_snapshot(client)
    assert len(events) == preview["result_count"]
    current_uids = {str(e["UID"]) for e in events}
    assert current_uids < old_uids
    assert all(not str(e["SUMMARY"]).startswith("[已移除]") for e in events)
    with sessions() as db:
        removed = db.scalar(select(Projection).where(Projection.event_id == ids["a"]))
        assert removed.removed
        assert old_uids - current_uids == {f"{removed.id}@calendar.anke-sports"}


def test_follow_changes_reject_retired_creator_settings(stack):
    client, sessions = stack
    setup_schedule(sessions)
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        with pytest.raises(ValidationError):
            Config.model_validate(
                {
                    **user.config,
                    "creators": [{"channel_id": "creator-a", "scope_keys": ["test:a"]}],
                }
            )
        assert "creators" not in user.config


def test_preview_addition_counts_unknown_dates_and_limits_examples_after_hashing(stack):
    client, sessions = stack
    setup_schedule(sessions)
    with sessions() as db:
        for n in range(15):
            start = datetime.now(timezone.utc) + timedelta(days=n + 10)
            db.add(
                Event(
                    source_key=f"test:new:{n}",
                    competition_id="test:league",
                    sport="basketball",
                    title=f"新场次 {n}",
                    starts_at=start.isoformat(),
                    local_date=start.date().isoformat(),
                    provider="test",
                    participants=[{"id": "test:c"}],
                    demo=True,
                )
            )
        db.add(
            Event(
                source_key="test:unknown",
                competition_id="test:league",
                sport="basketball",
                title="时间未知",
                time_precision="unknown",
                provider="test",
                participants=[{"id": "test:c"}],
                demo=True,
            )
        )
        db.commit()
    body = payload(client, [follow("a"), follow("b"), follow("c")])
    first = client.post("/api/v1/me/follows/preview", json=body).json()
    assert first["added"]["total"] == 15 and len(first["added"]["items"]) == 10
    assert first["undated_count"] == 1 and first["result_count"] == 20
    with sessions() as db:
        # Outside displayed examples, still bound to the review confirmation.
        event = db.scalar(select(Event).where(Event.source_key == "test:new:14"))
        event.title = "更新后的场次"
        db.commit()
    stale = client.put("/api/v1/me/follows", json={**body, "confirmation": first["confirmation"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "FOLLOWS_PREVIEW_CHANGED"
    fresh = client.post("/api/v1/me/follows/preview", json=body).json()
    assert fresh["confirmation"] != first["confirmation"]
    assert (
        client.put("/api/v1/me/follows", json={**body, "confirmation": fresh["confirmation"]}).status_code
        == 200
    )
    drain()
    with sessions() as db:
        feed = db.scalar(select(Feed).where(Feed.owner_id == "local-reviewer"))
        assert (
            len(
                db.scalars(
                    select(Projection).where(Projection.feed_id == feed.id, Projection.removed.is_(False))
                ).all()
            )
            == fresh["result_count"]
        )


def test_preview_canonical_follows_and_revision_conflict_do_not_mutate(stack):
    client, sessions = stack
    setup_schedule(sessions)
    body = payload(client, [follow("b"), follow("a"), follow("b")])
    first = client.post("/api/v1/me/follows/preview", json=body).json()
    canonical = client.post(
        "/api/v1/me/follows/preview", json={**body, "follows": [follow("a"), follow("b")]}
    ).json()
    assert first == canonical
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        save_config(db, user, {**user.config, "follows": [follow("a")]}, user.revision)
        db.commit()
    original = snapshot(sessions)
    for method, path in [(client.post, "/api/v1/me/follows/preview"), (client.put, "/api/v1/me/follows")]:
        result = method(path, json={**body, "confirmation": first["confirmation"]})
        assert result.status_code == 409 and result.json()["error"]["code"] == "REVISION_CONFLICT"
    assert snapshot(sessions) == original


def test_preview_is_personal_and_requires_authentication(stack):
    client, sessions = stack
    setup_schedule(sessions)
    with sessions() as db:
        other = ensure_user(db, "other-user")
        save_config(db, other, {**other.config, "follows": [follow("a")]}, other.revision)
        rebuild_feed(db, other.id)
        db.commit()
    body = payload(client, [])
    result = client.post("/api/v1/me/follows/preview", json=body).json()
    assert result["removed"]["total"] == 3 and result["result_count"] == 2
    assert not any("token" in key or "url" in key for key in result)
    client.cookies.clear()
    before = snapshot(sessions)
    assert client.post("/api/v1/me/follows/preview", json=body).status_code == 401
    assert snapshot(sessions) == before


def test_paused_preview_is_next_publication_and_pending_changes_are_explicit(stack):
    client, sessions = stack
    setup_schedule(sessions)
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
        feed.paused = True
        save_config(db, user, {**user.config, "follows": [follow("b")]}, user.revision)
        db.commit()
    preview = client.post("/api/v1/me/follows/preview", json=payload(client, [])).json()
    assert preview["feed_paused"] and preview["publication_pending"]
    assert preview["removed"]["total"] == 3 and preview["result_count"] == 2


def test_date_only_today_and_offsets_use_actual_event_time():
    instant = datetime(2026, 9, 10, 0, 30, tzinfo=timezone.utc)
    event = SimpleNamespace(
        time_precision="date_only", starts_at=None, local_date="2026-09-09", timezone="America/Los_Angeles"
    )
    assert not event_is_past(event, instant)  # Still Sep 9 locally, start time unknown.
    event.local_date = "2026-09-08"
    assert event_is_past(event, instant)
    event.time_precision = "exact"
    event.starts_at = "2026-09-09T23:00:00-07:00"
    assert not event_is_past(event, instant)
    event.starts_at = "2026-09-10T08:00:00+08:00"
    assert event_is_past(event, instant)
