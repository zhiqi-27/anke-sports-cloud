"""Real independent SQLite connections; synthetic upstreams, never cloud evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event as Signal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, func

from app import content, jobs, websub, worker
from app.config import settings
from app.db import ChannelSync, ChannelWork, Job, NotificationReceipt, Video
from tests.test_content import CHANNEL, VID, api_video, setup_content, prepare_hook, hook_body, sign
from tests.test_jobs import job


def queue(sessions, kind="youtube_videos", channel=CHANNEL, **extra):
    return job(sessions, kind, {"channel_id": channel, "video_ids": [VID], **extra})


@pytest.mark.parametrize(
    "kind", ["youtube_poll", "youtube_videos", "youtube_channel_metadata", "youtube_rematch"]
)
def test_channel_data_jobs_share_one_lease_but_hub_and_other_channels_can_run(stack, kind):
    _, sessions = stack
    first = queue(sessions)
    second = queue(sessions, kind)
    hub = queue(sessions, "youtube_subscribe")
    other = queue(sessions, channel="UC" + "b" * 22)
    with sessions() as db:
        claim, _ = jobs.claim_job(db, first)
        db.commit()
    with sessions() as db:
        blocked, progressed = jobs.claim_job(db, second)
        assert blocked is None and progressed
        row = db.get(Job, second)
        assert row.attempts == 0 and jobs.now() < row.due_at < claim.lease
        assert jobs.claim_job(db, hub)[0] is not None
        assert jobs.claim_job(db, other)[0] is not None
        db.commit()


@pytest.mark.parametrize("late_error", [False, True])
def test_different_job_after_expiry_fences_old_metadata_and_error(disk_stack, monkeypatch, late_error):
    sessions, _ = disk_stack
    setup_content(sessions)
    first, second = queue(sessions), queue(sessions)
    entered, release = Signal(), Signal()
    calls = []

    def request(*_):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            if late_error:
                raise ValueError("OLD_RESPONSE_ERROR")
            return {"items": [api_video("obsolete metadata")]}
        return {"items": [api_video("fresh metadata")]}

    monkeypatch.setattr(content, "youtube_request", request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(worker.run_one, first)
        assert entered.wait(5)
        monkeypatch.setattr(
            jobs, "now", lambda: (datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat()
        )
        try:
            assert worker.run_one(second)
        finally:
            release.set()
        assert old.result(timeout=5)
    with sessions() as db:
        assert db.get(Video, VID).title == "fresh metadata"
        assert db.get(Job, second).state == "done"
        assert db.get(Job, first).state == "running"  # Uncommitted old result; eligible for later recovery.
        assert db.get(ChannelWork, CHANNEL + ":data").error == ""
        assert db.get(ChannelSync, CHANNEL).error == ""


def test_channel_rate_limit_delays_network_but_not_local_rematching(stack, monkeypatch):
    _, sessions = stack
    setup_content(sessions)
    instant = datetime.now(timezone.utc) + timedelta(seconds=1)
    monkeypatch.setattr(jobs, "now", lambda: instant.isoformat())
    request = httpx.Request("GET", "https://example.invalid/?key=do-not-log")
    response = httpx.Response(429, headers={"Retry-After": "3600"}, request=request)
    monkeypatch.setattr(content, "youtube_request", lambda *_: response.raise_for_status())
    first = queue(sessions)
    assert worker.run_one(first)
    second = queue(sessions, "youtube_poll")
    rematch = queue(sessions, "youtube_rematch", user_id="local-reviewer")
    with sessions() as db:
        state = db.get(ChannelWork, CHANNEL + ":data")
        assert state.error == "UPSTREAM_RATE_LIMITED"
        assert datetime.fromisoformat(state.next_attempt_at) >= instant + timedelta(hours=1)
        assert jobs.claim_job(db, second)[0] is None
        assert db.get(Job, second).attempts == 0
        local, _ = jobs.claim_job(db, rematch)
        assert local
        jobs.complete_job(db, local)
        db.commit()
        assert state.error == "UPSTREAM_RATE_LIMITED" and state.consecutive_failures == 1
    # Successful fetch clears the network error; a local match does not falsely report recovery.
    instant += timedelta(hours=2)
    monkeypatch.setattr(content, "youtube_request", lambda *_: {"items": [api_video("new metadata")]})
    assert worker.run_one(first)
    with sessions() as db:
        assert db.get(ChannelWork, CHANNEL + ":data").error == ""
        assert content.creator_status(db, CHANNEL)["sync_status"] != "error"
        from app.db import Creator

        assert db.get(Creator, CHANNEL).last_error == ""


def test_exhausted_channel_crash_reports_failure_and_releases_its_lease(stack, monkeypatch):
    _, sessions = stack
    setup_content(sessions)
    ident = queue(sessions)
    instant = datetime.now(timezone.utc) + timedelta(seconds=1)
    for attempt in range(1, 6):
        monkeypatch.setattr(jobs, "now", lambda: instant.isoformat())
        claimed = claim(sessions, ident)
        assert claimed.attempt == attempt
        instant += timedelta(minutes=6)
    monkeypatch.setattr(jobs, "now", lambda: instant.isoformat())
    with sessions() as db:
        assert jobs.claim_job(db, ident) == (None, True)
        db.commit()
        assert db.get(Job, ident).state == "failed"
        state = db.get(ChannelWork, CHANNEL + ":data")
        assert state.lease_job_id is None and state.error == "WORKER_LEASE_EXPIRED"
        assert content.creator_status(db, CHANNEL)["sync_status"] == "error"


def hub_settings(monkeypatch, sessions):
    monkeypatch.setattr(websub, "SessionLocal", sessions)
    cfg = SimpleNamespace(
        youtube_websub_enabled=True, public_url="https://fixture.example", cipher=settings().cipher
    )
    monkeypatch.setattr(websub, "settings", lambda: cfg)


def claim(sessions, ident):
    with sessions() as db:
        value, _ = jobs.claim_job(db, ident)
        db.commit()
        return value


def test_expired_hub_worker_cannot_replace_new_intent(stack, monkeypatch):
    _, sessions = stack
    setup_content(sessions)
    hub_settings(monkeypatch, sessions)
    old = claim(sessions, queue(sessions, "youtube_subscribe"))
    monkeypatch.setattr(
        jobs, "now", lambda: (datetime.fromisoformat(old.lease) + timedelta(seconds=1)).isoformat()
    )
    fresh = claim(sessions, queue(sessions, "youtube_subscribe"))
    with sessions() as db:
        sync = db.get(ChannelSync, CHANNEL)
        sync.state, sync.pending_until = (
            "pending",
            (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        )
        before = sync.pending_until
        db.commit()
    with pytest.raises(jobs.LeaseLost):
        websub.request_subscription(CHANNEL, old)
    with sessions() as db:
        assert db.get(ChannelSync, CHANNEL).pending_until == before
        assert db.get(ChannelWork, CHANNEL + ":hub").lease_job_id == fresh.id


def test_hub_duplicate_preserves_unexpired_intent_and_verification_retries(stack, monkeypatch):
    client, sessions = stack
    callback = prepare_hook(sessions)
    hub_settings(monkeypatch, sessions)
    pending = claim(sessions, queue(sessions, "youtube_subscribe"))
    # Must return before constructing an HTTP client while an intent awaits acknowledgement.
    monkeypatch.setattr(websub.httpx, "Client", lambda **_: pytest.fail("Duplicated Hub request"))
    with sessions() as db:
        before = db.get(ChannelSync, CHANNEL).pending_until
    websub.request_subscription(CHANNEL, pending)
    with sessions() as db:
        assert db.get(ChannelSync, CHANNEL).pending_until == before
    params = {
        "hub.mode": "subscribe",
        "hub.topic": websub.topic(CHANNEL),
        "hub.challenge": "repeat-challenge",
        "hub.lease_seconds": "1000",
    }
    path = "/webhooks/youtube/" + callback
    assert client.get(path, params=params).text == "repeat-challenge"
    with sessions() as db:
        expiry = db.get(ChannelSync, CHANNEL).lease_expires_at
    assert client.get(path, params=params).text == "repeat-challenge"
    assert client.get(path, params={**params, "hub.challenge": "different"}).status_code == 404
    assert client.get(path, params={**params, "hub.lease_seconds": "1001"}).status_code == 404
    with sessions() as db:
        assert db.get(ChannelSync, CHANNEL).lease_expires_at == expiry


def test_simultaneous_notifications_commit_one_receipt_and_one_refresh(disk_stack):
    sessions, _ = disk_stack
    callback = prepare_hook(sessions)
    with sessions() as db:
        sync = db.get(ChannelSync, CHANNEL)
        sync.state = "verified"
        sync.lease_expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        db.commit()
    body = hook_body()

    def receive():
        with sessions() as db:
            accepted = websub.notification(db, callback, body, sign(body))
            db.commit()
            return accepted

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(receive) for _ in range(2)]
        assert all(f.result(timeout=5) for f in results)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(NotificationReceipt)) == 1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "youtube_videos")) == 1


@pytest.mark.parametrize("decision", ["ignore", "remove_creator"])
def test_match_reloads_personal_choice_after_acquiring_user_lock(disk_stack, decision):
    from app.db import User, VideoMatch

    sessions, _ = disk_stack
    setup_content(sessions, both=False)
    with sessions() as old:
        stale_user = old.get(User, "local-reviewer")
        stale_match = old.scalar(select(VideoMatch).where(VideoMatch.owner_id == stale_user.id))
        video = old.get(Video, VID)
        with sessions() as current:
            user = current.get(User, stale_user.id)
            if decision == "ignore":
                content.decide_review(
                    current, user, stale_match.id, "ignore", "preview", stale_match.updated_at
                )
            else:
                content.remove_creator(current, user, CHANNEL, user.revision)
            current.commit()
        content.match_video(old, video)
        old.commit()
        old.refresh(stale_match)
        assert stale_match.decision == ("ignored" if decision == "ignore" else "retired")
        assert stale_user.revision > 1


def test_review_reloads_cached_match_before_accepting_old_version(disk_stack):
    from app.db import User, VideoMatch
    from fastapi import HTTPException

    sessions, _ = disk_stack
    setup_content(sessions, both=False)
    with sessions() as old:
        user = old.get(User, "local-reviewer")
        match = old.scalar(select(VideoMatch).where(VideoMatch.owner_id == user.id))
        version = match.updated_at
        with sessions() as current:
            row = current.get(VideoMatch, match.id)
            row.updated_at = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
            current.commit()
        with pytest.raises(HTTPException) as error:
            content.decide_review(old, user, match.id, "confirm", "preview", version)
        assert error.value.detail["code"] == "REVIEW_CHANGED"
        old.rollback()


def test_concurrent_channel_schedulers_enqueue_one_shared_poll(disk_stack):
    sessions, _ = disk_stack
    setup_content(sessions)

    def schedule():
        with sessions() as db:
            content.enqueue_channel(db, CHANNEL)
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(schedule) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "youtube_poll")) == 1


def test_title_only_refresh_preserves_label_and_url_personal_ics(stack, monkeypatch):
    from tests.test_calendar_flow import drain, feed_snapshot

    client, sessions = stack
    callback = prepare_hook(sessions)
    path = "/webhooks/youtube/" + callback
    assert (
        client.get(
            path,
            params={
                "hub.mode": "subscribe",
                "hub.topic": websub.topic(CHANNEL),
                "hub.challenge": "ics-pipeline",
                "hub.lease_seconds": "3600",
            },
        ).status_code
        == 200
    )
    _, before, before_events = feed_snapshot(client)
    with sessions() as db:
        title = db.get(Video, VID).title + " updated"
    monkeypatch.setattr(content, "youtube_request", lambda *_: {"items": [api_video(title)]})
    body = hook_body()
    assert client.post(path, content=body, headers={"X-Hub-Signature": sign(body)}).status_code == 204
    drain()
    _, refreshed, refreshed_events = feed_snapshot(client)
    assert str(before_events[0]["UID"]) == str(refreshed_events[0]["UID"])
    assert int(refreshed_events[0]["SEQUENCE"]) == int(before_events[0]["SEQUENCE"])
    assert "updated" not in str(refreshed_events[0]["DESCRIPTION"])
    assert before.headers["etag"] == refreshed.headers["etag"]
    assert before.content == refreshed.content
    assert client.post(path, content=body, headers={"X-Hub-Signature": sign(body)}).status_code == 204
    drain()
    assert feed_snapshot(client)[1].content == refreshed.content


@pytest.mark.parametrize("kind", ["video", "creator"])
def test_expiration_scan_does_not_remove_concurrently_refreshed_metadata(disk_stack, monkeypatch, kind):
    from app.db import Creator
    from sqlalchemy import event as sql_event

    sessions, _ = disk_stack
    setup_content(sessions)
    model, ident = (Video, VID) if kind == "video" else (Creator, CHANNEL)
    with sessions() as db:
        row = db.get(model, ident)
        row.updated_at = (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()
        db.commit()
    entered, release = Signal(), Signal()
    with sessions() as old:
        connection = old.connection()

        def pause_before_expiry(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("UPDATE " + model.__tablename__ + " "):
                entered.set()
                assert release.wait(5)

        sql_event.listen(connection, "before_cursor_execute", pause_before_expiry)

        def expire():
            content.expire_metadata(old)
            old.commit()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(expire)
            assert entered.wait(5)
            try:
                with sessions() as current:
                    row = current.get(model, ident)
                    row.updated_at = datetime.now(timezone.utc).isoformat()
                    if kind == "video":
                        row.title = "Fresh video title"
                    else:
                        row.name = "Fresh creator name"
                    current.commit()
            finally:
                release.set()
            future.result(timeout=5)
    with sessions() as db:
        row = db.get(model, ident)
        assert (
            (row.title == "Fresh video title" and row.available)
            if kind == "video"
            else row.name == "Fresh creator name"
        )
