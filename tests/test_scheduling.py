from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import event as sql_event, func, select, update

from app import broadcasts, worker
from app.config import settings
from app.db import BroadcastRecord, Event, Job, Link, ProviderState
from app.providers import enqueue_provider, upsert_event
from tests.test_broadcasts import publish, setup_record
from tests.test_calendar_flow import drain, feed_snapshot


def test_worker_startup_checks_persisted_deadlines_without_refreshing_on_every_restart(stack, monkeypatch):
    _, sessions = stack
    instant = datetime.now(timezone.utc)
    with sessions() as db:
        db.add(
            ProviderState(id="jolpica", enabled=True, last_success=(instant - timedelta(hours=7)).isoformat())
        )
        db.commit()
    calls = []

    def fetch(db, ident):
        calls.append(ident)
        db.get(ProviderState, ident).last_success = datetime.now(timezone.utc).isoformat()

    monkeypatch.setattr(worker, "sync_provider", fetch)
    monkeypatch.setattr(worker, "run_maintenance", lambda: True)
    assert worker.main(["--once"]) == 0
    assert calls == ["jolpica"]
    assert worker.main(["--once"]) == 0
    assert calls == ["jolpica"]
    with sessions() as db:
        completed_at = datetime.fromisoformat(db.get(ProviderState, "jolpica").last_success)
    monkeypatch.setattr(worker, "now", lambda: (completed_at + timedelta(hours=6)).isoformat())
    assert worker.schedule_providers() == 1
    assert worker.schedule_providers() == 0


def test_provider_retry_cooldown_recent_attempt_and_disabled_are_not_rescheduled(stack, monkeypatch):
    _, sessions = stack
    instant = datetime.now(timezone.utc)
    old = (instant - timedelta(hours=7)).isoformat()
    with sessions() as db:
        db.add_all(
            [
                ProviderState(
                    id="jolpica",
                    enabled=True,
                    last_success=old,
                    next_attempt_at=(instant + timedelta(hours=1)).isoformat(),
                ),
                ProviderState(id="balldontlie", enabled=True, last_attempt_at=instant.isoformat()),
                ProviderState(id="football-data", enabled=False, last_success=old),
            ]
        )
        db.commit()
    assert worker.schedule_providers() == 0
    monkeypatch.setattr(worker, "now", lambda: (instant + timedelta(hours=1)).isoformat())
    assert worker.schedule_providers() == 1
    # A pre-existing delayed/running job remains the recovery owner even if another period elapses.
    monkeypatch.setattr(worker, "now", lambda: (instant + timedelta(hours=7)).isoformat())
    assert worker.schedule_providers() == 1  # Only balldontlie becomes due now.


def test_manual_and_timer_provider_requests_share_one_job_across_connections(disk_stack):
    sessions, _ = disk_stack
    with sessions() as db:
        db.add(ProviderState(id="jolpica", enabled=True))
        db.commit()
    barrier = Barrier(6)

    def request(i):
        barrier.wait(timeout=5)
        if i % 2:
            return worker.schedule_providers()
        with sessions() as db:
            queued = enqueue_provider(db, "jolpica")
            db.commit()
            return queued

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(request, range(6))) == 1
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "provider")) == 1


def test_repeated_manual_provider_http_request_does_not_append_work(stack):
    client, sessions = stack
    for _ in range(2):
        assert client.post("/api/v1/local/providers/jolpica/sync").json() == {"queued": True}
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "provider")) == 1


def test_offset_expiry_and_legacy_normalization_work_with_network_disabled(stack, monkeypatch):
    client, sessions = stack
    _, record = setup_record(stack, monkeypatch)
    instant = datetime.now(timezone.utc)
    expiry = instant + timedelta(hours=2)
    approved = publish(
        client, record, valid_until=expiry.astimezone(timezone(timedelta(hours=-11))).isoformat()
    ).json()
    assert approved["published"]["valid_until"] == expiry.isoformat()
    drain()
    _, before, entries = feed_snapshot(client)
    uid, sequence = str(entries[0]["UID"]), int(entries[0]["SEQUENCE"])
    with sessions() as db:
        row = db.get(BroadcastRecord, record["id"])
        row.expires_at = ""  # The migration's legacy marker, with an actual offset publication.
        row.published = {
            **row.published,
            "valid_until": expiry.astimezone(timezone(timedelta(hours=-11))).isoformat(),
        }
        db.commit()
    monkeypatch.setattr(settings(), "broadcast_checks_enabled", False)
    monkeypatch.setattr(broadcasts, "now", lambda: instant.isoformat())
    assert broadcasts.schedule_broadcasts()["normalized"] == 1
    assert broadcasts.schedule_broadcasts()["examined"] == 0
    with sessions() as db:
        assert db.get(BroadcastRecord, record["id"]).expires_at == expiry.isoformat()
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "broadcast_check")) == 0
    monkeypatch.setattr(broadcasts, "now", lambda: expiry.isoformat())
    assert broadcasts.schedule_broadcasts()["expired"] == 1
    drain()
    _, after, entries = feed_snapshot(client)
    assert after.content != before.content
    assert str(entries[0]["UID"]) == uid and int(entries[0]["SEQUENCE"]) == sequence + 1
    assert "abcdefghijk" not in str(entries[0]["DESCRIPTION"])


@pytest.mark.parametrize("hours,expected", [(25, 1), (26, 2), (72, 6), (-1, 6)])
def test_network_check_persists_next_hourly_window_boundary(stack, monkeypatch, hours, expected):
    client, sessions = stack
    ident, record = setup_record(stack, monkeypatch)
    publish(client, record).raise_for_status()
    instant = datetime.now(timezone.utc)
    with sessions() as db:
        db.get(Event, ident).starts_at = (instant + timedelta(hours=hours)).isoformat()
        db.commit()
    monkeypatch.setattr(broadcasts, "head_probe", lambda url: "reachable")
    with sessions() as db:
        broadcasts.check_record(db, record["id"], db.get(Link, record["id"]).url_hash)
        db.commit()
        row = db.get(BroadcastRecord, record["id"])
        elapsed = (broadcasts.utc_time(row.next_check_at) - instant).total_seconds()
        assert abs(elapsed - expected * 3600) < 2


def test_reschedule_wakes_inspection_but_does_not_change_review_evidence(stack, monkeypatch):
    client, sessions = stack
    ident, record = setup_record(stack, monkeypatch)
    publish(client, record).raise_for_status()
    instant = datetime.now(timezone.utc)
    with sessions() as db:
        row = db.get(BroadcastRecord, record["id"])
        revision, publication = row.revision, row.published
        row.next_check_at = (instant + timedelta(hours=6)).isoformat()
        db.commit()
        upsert_event(db, "test:a", starts_at=(instant + timedelta(hours=2)).isoformat())
        db.commit()
        assert broadcasts.utc_time(row.next_check_at) < instant + timedelta(minutes=1)
        assert row.revision == revision and row.published == publication and row.network_checked_at is None


def test_broadcast_manual_and_timer_requests_deduplicate_and_rollback(disk_stack, monkeypatch):
    from app import db as database
    from app.broadcast_schemas import BroadcastDecision
    from tests.test_broadcasts import payload

    sessions, ident = disk_stack
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(settings(), "broadcast_checks_enabled", True)
    with sessions() as db:
        record = broadcasts.create_record(db, "fixture", payload(ident))
        broadcasts.approve_record(
            db,
            "fixture",
            record.link_id,
            BroadcastDecision(
                expected_revision=0,
                source_and_event_confirmed=True,
                valid_until=datetime.now(timezone.utc) + timedelta(days=2),
            ),
        )
        db.commit()
        link_id = record.link_id
    with sessions() as db:
        row = broadcasts.lock_record(db, link_id)
        broadcasts.enqueue_check(db, row)
        db.rollback()
    barrier = Barrier(6)

    def request(i):
        barrier.wait(timeout=5)
        if i % 2:
            return broadcasts.schedule_broadcasts()["queued"]
        with sessions() as db:
            row = broadcasts.lock_record(db, link_id)
            broadcasts.queue_check(db, "fixture", link_id, row.revision)
            db.commit()
            return 0

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(request, range(6)))
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "broadcast_check")) == 1


def test_broadcast_batch_advances_past_pending_jobs_and_never_hydrates_future_rows(stack, monkeypatch):
    from experiments.scheduler_capacity import seed_broadcasts

    _, sessions = stack
    seed_broadcasts(sessions, count=1200, due=9, expired=0)
    monkeypatch.setattr(settings(), "broadcast_checks_enabled", True)
    monkeypatch.setattr(broadcasts, "BROADCAST_BATCH", 4)
    loaded = []
    sql_event.listen(BroadcastRecord, "load", callback := lambda row, _: loaded.append(row.link_id))
    try:
        assert broadcasts.schedule_broadcasts()["queued"] == 4
        assert broadcasts.schedule_broadcasts()["queued"] == 4
        assert broadcasts.schedule_broadcasts()["queued"] == 1
        assert broadcasts.schedule_broadcasts()["examined"] == 0
        assert len(loaded) == 9
        # Delayed jobs already pending must not monopolize a limited candidate page.
        with sessions() as db:
            db.execute(
                update(BroadcastRecord)
                .where(BroadcastRecord.link_id.in_(loaded[:4]))
                .values(next_check_at="")
            )
            db.commit()
        assert broadcasts.schedule_broadcasts()["examined"] == 0
    finally:
        sql_event.remove(BroadcastRecord, "load", callback)


def test_new_publication_is_not_blocked_by_a_pending_check_for_the_old_url(stack, monkeypatch):
    client, sessions = stack
    _, record = setup_record(stack, monkeypatch)
    published = publish(client, record).json()
    monkeypatch.setattr(settings(), "broadcast_checks_enabled", True)
    assert broadcasts.schedule_broadcasts()["queued"] == 1
    edited = client.put(
        f"/api/v1/maintenance/broadcasts/{record['id']}",
        json={
            **record["draft"],
            "url": "https://www.nba.com/game/changed-fixture",
            "expected_revision": published["revision"],
        },
    ).json()
    publish(client, edited).raise_for_status()
    assert broadcasts.schedule_broadcasts()["queued"] == 1
    calls = []
    monkeypatch.setattr(broadcasts, "head_probe", lambda url: calls.append(url) or "reachable")
    with sessions() as db:
        checks = db.scalars(select(Job.id).where(Job.kind == "broadcast_check")).all()
    for ident in checks:
        assert worker.run_one(ident)
    assert calls == ["https://www.nba.com/game/changed-fixture"]


def test_expiry_candidate_rechecks_publication_after_concurrent_reapproval(disk_stack, monkeypatch):
    from app import db as database
    from app.broadcast_schemas import BroadcastDecision
    from tests.test_broadcasts import payload

    sessions, event_id = disk_stack
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(settings(), "broadcast_checks_enabled", False)
    instant = datetime.now(timezone.utc)
    with sessions() as db:
        record = broadcasts.create_record(db, "fixture", payload(event_id))
        decision = BroadcastDecision(
            expected_revision=0, source_and_event_confirmed=True, valid_until=instant + timedelta(days=1)
        )
        broadcasts.approve_record(db, "fixture", record.link_id, decision)
        record.expires_at = (instant - timedelta(seconds=1)).isoformat()
        record.published = {**record.published, "valid_until": record.expires_at}
        db.commit()
        ident = record.link_id
    original_lock = broadcasts.lock_record
    renewed = []

    def raced_lock(db, key):
        assert not renewed
        with sessions() as other:
            current = other.get(BroadcastRecord, key)
            broadcasts.approve_record(
                other, "fixture", key, decision.model_copy(update={"expected_revision": current.revision})
            )
            other.commit()
        renewed.append(key)
        return original_lock(db, key)

    monkeypatch.setattr(broadcasts, "lock_record", raced_lock)
    assert broadcasts.schedule_broadcasts()["expired"] == 0
    with sessions() as db:
        assert db.get(BroadcastRecord, ident).status == "published"
        assert db.get(Link, ident).available
