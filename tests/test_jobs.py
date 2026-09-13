from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from threading import Event as Signal

import httpx
import pytest
from sqlalchemy import select

from app import jobs, worker
from app.calendar import rebuild_feed
from app.db import Event, Feed, Job, JobReplay, Projection, ProviderState, User
from app.service import enqueue, save_config
from tests.test_calendar_flow import insert_event


def job(sessions, kind="provider", payload=None):
    with sessions() as db:
        row = Job(kind=kind, payload=payload or {"provider": "jolpica"})
        db.add(row)
        db.commit()
        return row.id


@pytest.mark.parametrize("late_error", [False, True])
def test_reclaimed_worker_cannot_commit_data_jobs_or_error_over_new_owner(
    disk_stack, monkeypatch, late_error
):
    sessions, event_id = disk_stack
    job_id = job(sessions, "fixture", {"event_id": event_id})
    entered, release = Signal(), Signal()

    def execute(db, claim):
        if claim.attempt == 1:
            entered.set()
            assert release.wait(5)
            if late_error:
                raise RuntimeError("sensitive://late-worker-value")
        db.get(Event, event_id).title = "stale" if claim.attempt == 1 else "new owner"
        enqueue(db, "projection", {"user_id": "local-reviewer"})

    monkeypatch.setattr(worker, "execute_claim", execute)
    with ThreadPoolExecutor(max_workers=1) as executor:
        old = executor.submit(worker.run_one, job_id)
        assert entered.wait(5)
        # Advance the protocol clock beyond its committed lease; no main database mutation.
        future = datetime.now(timezone.utc) + timedelta(minutes=6)
        monkeypatch.setattr(jobs, "now", lambda: future.isoformat())
        try:
            assert worker.run_one(job_id)
        finally:
            release.set()
        assert old.result(timeout=5)
    with sessions() as db:
        row = db.get(Job, job_id)
        assert (row.state, row.attempts, row.error) == ("done", 2, "")
        assert db.get(Event, event_id).title == "new owner"
        assert len(db.scalars(select(Job).where(Job.kind == "projection", Job.state == "pending")).all()) == 1


def test_crashes_exhaust_attempt_budget_without_running_a_sixth_attempt(stack, monkeypatch):
    _, sessions = stack
    job_id = job(sessions, "fixture")
    instant = datetime.now(timezone.utc) + timedelta(seconds=1)
    for attempt in range(1, 6):
        monkeypatch.setattr(jobs, "now", lambda: instant.isoformat())
        with sessions() as db:
            claim, changed = jobs.claim_job(db, job_id)
            db.commit()
            assert changed and claim.attempt == attempt
        instant += timedelta(minutes=6)
    monkeypatch.setattr(jobs, "now", lambda: instant.isoformat())
    monkeypatch.setattr(worker, "execute_claim", lambda *_: pytest.fail("Exhausted job executed"))
    assert worker.run_one(job_id)
    with sessions() as db:
        row = db.get(Job, job_id)
        assert (row.state, row.attempts, row.error) == ("failed", 5, "WORKER_LEASE_EXPIRED")
    assert worker.run_one(job_id) is False


def test_provider_single_flight_defers_competing_job_without_charging_attempt(stack):
    _, sessions = stack
    first, second = job(sessions), job(sessions)
    with sessions() as db:
        claim, _ = jobs.claim_job(db, first)
        db.commit()
    with sessions() as db:
        other, progressed = jobs.claim_job(db, second)
        db.commit()
        assert other is None and progressed
        row = db.get(Job, second)
        assert row.state == "pending" and row.attempts == 0 and jobs.now() < row.due_at < claim.lease
        assert db.get(ProviderState, "jolpica").lease_job_id == first


def test_three_provider_failures_open_circuit_and_success_closes_it(stack, monkeypatch):
    client, sessions = stack
    event_id = insert_event(sessions)
    job_id = job(sessions)
    calls = []

    def unavailable(db, provider):
        calls.append(provider)
        db.get(Event, event_id).title = "must roll back"
        raise ValueError("INCOMPLETE_PAGINATION")

    monkeypatch.setattr(worker, "sync_provider", unavailable)
    instant = datetime.now(timezone.utc) + timedelta(seconds=1)
    for count in range(1, 4):
        monkeypatch.setattr(jobs, "now", lambda: instant.isoformat())
        assert worker.run_one(job_id)
        with sessions() as db:
            state = db.get(ProviderState, "jolpica")
            assert state.consecutive_failures == count and state.last_success is None
            assert db.get(Event, event_id).title == "测试比赛"
            deadline = datetime.fromisoformat(state.next_attempt_at)
            if count == 3:
                assert deadline - instant >= timedelta(minutes=15)
            instant = deadline + timedelta(seconds=1)
    reported = next(p for p in client.get("/api/v1/status").json()["providers"] if p["id"] == "jolpica")
    assert reported["consecutive_failures"] == 3 and reported["next_attempt_at"]
    monkeypatch.setattr(jobs, "now", lambda: (deadline - timedelta(seconds=1)).isoformat())
    assert worker.run_one(job_id) is False
    assert len(calls) == 3
    monkeypatch.setattr(jobs, "now", lambda: instant.isoformat())

    def recovered(db, provider):
        db.get(Event, event_id).title = "恢复后的完整赛程"
        db.get(ProviderState, provider).last_success = jobs.now()

    monkeypatch.setattr(worker, "sync_provider", recovered)
    assert worker.run_one(job_id)
    with sessions() as db:
        state = db.get(ProviderState, "jolpica")
        assert (
            state.consecutive_failures == 0 and state.next_attempt_at is None and state.lease_job_id is None
        )
        assert db.get(Event, event_id).title == "恢复后的完整赛程"
        assert db.get(Job, job_id).state == "done"


@pytest.mark.parametrize("http_date", [False, True])
def test_rate_limit_wait_is_respected_without_logging_request_credentials(stack, monkeypatch, http_date):
    _, sessions = stack
    instant = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)
    monkeypatch.setattr(jobs, "now", lambda: instant.isoformat())
    value = format_datetime(instant + timedelta(hours=1), usegmt=True) if http_date else "3600"
    request = httpx.Request("GET", "https://example.invalid/?key=private-test-value")
    response = httpx.Response(429, headers={"Retry-After": value}, request=request)

    def limited(*_):
        response.raise_for_status()

    monkeypatch.setattr(worker, "sync_provider", limited)
    job_id = job(sessions)
    assert worker.run_one(job_id)
    with sessions() as db:
        row = db.get(Job, job_id)
        assert row.error == "UPSTREAM_RATE_LIMITED" and row.state == "pending"
        assert datetime.fromisoformat(row.due_at) >= instant + timedelta(hours=1)
        state = db.get(ProviderState, "jolpica")
        assert state.error == row.error and state.next_attempt_at == row.due_at


def test_new_provider_job_fences_expired_different_job_result(stack, monkeypatch):
    _, sessions = stack
    first, second = job(sessions), job(sessions)
    with sessions() as db:
        old, _ = jobs.claim_job(db, first)
        db.commit()
    monkeypatch.setattr(
        jobs, "now", lambda: (datetime.fromisoformat(old.lease) + timedelta(seconds=1)).isoformat()
    )
    with sessions() as db:
        new, _ = jobs.claim_job(db, second)
        db.commit()
    with sessions() as db:
        db.get(User, "local-reviewer").display_name = "stale result"
        with pytest.raises(jobs.LeaseLost):
            jobs.complete_job(db, old)
        db.rollback()
    with sessions() as db:
        assert db.get(User, "local-reviewer").display_name != "stale result"
        assert db.get(ProviderState, "jolpica").lease_job_id == new.id


def test_result_and_projection_roll_back_when_completion_storage_fails(stack, monkeypatch):
    _, sessions = stack
    event_id = insert_event(sessions)
    job_id = job(sessions)

    def fetched(db, _):
        db.get(Event, event_id).title = "uncommitted fetch"

    def broken(*_):
        raise RuntimeError("simulated completion failure")

    monkeypatch.setattr(worker, "sync_provider", fetched)
    monkeypatch.setattr(worker, "complete_job", broken)
    assert worker.run_one(job_id)
    with sessions() as db:
        assert db.get(Event, event_id).title == "测试比赛"
        assert db.get(Job, job_id).state == "pending"
        assert db.scalars(select(Job).where(Job.kind == "projection", Job.state == "pending")).all() == []


def test_operator_replay_is_dry_by_default_and_keeps_one_auditable_successor(stack):
    client, sessions = stack
    original = job(sessions, "projection", {"user_id": "local-reviewer"})
    with sessions() as db:
        row = db.get(Job, original)
        row.state, row.attempts, row.error = "failed", 5, "RuntimeError"
        db.commit()
        preview = jobs.replay_job(db, original, 5, "已修复依赖")
        assert preview["dry_run"] and not preview["created"]
        assert db.get(JobReplay, original) is None
        created = jobs.replay_job(db, original, 5, "已修复依赖", True)
        db.commit()
        repeated = jobs.replay_job(db, original, 5, "重复请求", True)
        db.commit()
        assert repeated["new_job_id"] == created["new_job_id"] and not repeated["created"]
        assert db.get(Job, original).state == "failed"
        assert db.get(JobReplay, original).reason == "已修复依赖"
    assert worker.run_one(created["new_job_id"])
    with sessions() as db:
        assert db.get(Job, created["new_job_id"]).state == "done"
    assert client.get("/api/v1/me/calendar").json()["feed"]["status"] == "published"


def test_deleted_owner_cannot_be_replayed_and_cancelled_running_job_cannot_commit(disk_stack, monkeypatch):
    sessions, event_id = disk_stack
    original = job(sessions, "projection", {"user_id": "local-reviewer"})
    with sessions() as db:
        db.get(Job, original).state = "failed"
        db.get(User, "local-reviewer").deleted = True
        db.commit()
        with pytest.raises(ValueError, match="OWNER_UNAVAILABLE"):
            jobs.replay_job(db, original, 0, "不得恢复已删除账号", True)
    running = job(sessions, "fixture", {"event_id": event_id})
    entered, release = Signal(), Signal()

    def execute(db, claim):
        entered.set()
        assert release.wait(5)
        db.get(Event, event_id).title = "late cancelled write"

    monkeypatch.setattr(worker, "execute_claim", execute)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(worker.run_one, running)
        assert entered.wait(5)
        with sessions() as db:
            db.delete(db.get(Job, running))
            db.commit()
        release.set()
        assert pending.result(timeout=5)
    with sessions() as db:
        assert db.get(Event, event_id).title == "测试比赛"


def test_projection_refreshes_cached_config_after_acquiring_owner_lock(disk_stack):
    sessions, event_id = disk_stack
    with sessions() as old:
        stale = old.get(User, "local-reviewer")
        old_revision = stale.revision
        with sessions() as current:
            owner = current.get(User, stale.id)
            save_config(
                current,
                owner,
                {**owner.config, "event_overrides": [{"event_key": "test:a", "state": "exclude"}]},
                owner.revision,
            )
            current.commit()
        rebuild_feed(old, stale.id)
        old.commit()
        assert stale.revision == old_revision + 1
        projection = old.scalar(select(Projection).where(Projection.event_id == event_id))
        assert projection.removed
        assert "VEVENT" not in old.scalar(select(Feed)).body


def test_broken_maintenance_does_not_starve_persisted_jobs(stack, monkeypatch, caplog):
    from app import broadcasts, oauth, websub

    _, sessions = stack
    pending = job(sessions, "projection", {"user_id": "local-reviewer"})
    completed = []

    def broken():
        raise RuntimeError("do-not-log-private-details")

    monkeypatch.setattr(websub, "schedule_content", broken)
    monkeypatch.setattr(oauth, "clean_expired_connections", lambda: completed.append("oauth"))
    monkeypatch.setattr(broadcasts, "schedule_broadcasts", lambda: completed.append("broadcasts"))
    assert worker.main(["--once"]) == 1
    with sessions() as db:
        assert db.get(Job, pending).state == "done"
    assert completed == ["oauth", "broadcasts"]
    assert "MAINTENANCE_FAILED" in caplog.text and "do-not-log-private-details" not in caplog.text


def test_provider_keys_load_from_local_env_file_without_entering_settings_repr(tmp_path, monkeypatch):
    from app.config import Settings
    from app import providers

    for name in ("BALLDONTLIE_API_KEY", "FOOTBALL_DATA_API_KEY", "YOUTUBE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config_file = tmp_path / ".env"
    config_file.write_text(
        "BALLDONTLIE_API_KEY=fixture-nba-key\nFOOTBALL_DATA_API_KEY=fixture-football-key\nYOUTUBE_API_KEY=fixture-youtube-key\n"
    )
    config = Settings(_env_file=config_file)
    monkeypatch.setattr(providers, "settings", lambda: config)
    assert providers.provider_key("BALLDONTLIE_API_KEY") == "fixture-nba-key"
    assert providers.provider_key("FOOTBALL_DATA_API_KEY") == "fixture-football-key"
    assert providers.provider_key("YOUTUBE_API_KEY") == "fixture-youtube-key"
    assert "fixture-nba-key" not in repr(config) and "fixture-nba-key" not in config.model_dump_json()
    monkeypatch.setenv("BALLDONTLIE_API_KEY", "")
    assert providers.provider_key("BALLDONTLIE_API_KEY") == ""


def test_provider_activity_distinguishes_executing_queued_and_deferred_work(stack):
    client, sessions = stack
    first, second = job(sessions), job(sessions)
    with sessions() as db:
        db.add(ProviderState(id="jolpica"))
        db.commit()

    def activity():
        return next(p for p in client.get("/api/v1/status").json()["providers"] if p["id"] == "jolpica")[
            "activity"
        ]

    assert activity() == "queued"
    with sessions() as db:
        claim, _ = jobs.claim_job(db, first)
        db.commit()
        jobs.claim_job(db, second)
        db.commit()
    assert activity() == "running"
    with sessions() as db:
        jobs.complete_job(db, claim)
        db.commit()
    assert activity() == "waiting"
