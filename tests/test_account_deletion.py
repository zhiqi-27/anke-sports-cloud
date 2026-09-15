import copy

import pytest
from fastapi import HTTPException

from app.db import User
from app.privacy import delete_account_data
from app.service import save_config


def test_request_started_before_deletion_cannot_restore_personal_config(disk_stack):
    sessions, _ = disk_stack
    with sessions() as stale:
        user = stale.get(User, "local-reviewer")
        previous_config, previous_revision = copy.deepcopy(user.config), user.revision
        with sessions() as deletion:
            delete_account_data(deletion, user.id)
            deletion.commit()
        with pytest.raises(HTTPException):
            save_config(stale, user, previous_config, previous_revision)
        stale.rollback()
    with sessions() as db:
        assert db.get(User, "local-reviewer").config == {}


def test_http_deletion_revokes_feed_tokens_connections_and_only_the_owner(stack):
    from sqlalchemy import func, select
    from app.db import (
        CommandReceipt,
        Event,
        Feed,
        Job,
        JobReplay,
        Link,
        OAuthGrant,
        OAuthRequest,
        OAuthTokenRecord,
        Projection,
        Session,
        VideoMatch,
    )
    from app.service import ensure_user, attach_link
    from app.oauth import resource
    from tests.test_calendar_flow import insert_event
    from tests.test_oauth import register, authorize, exchange

    client, sessions = stack
    event_id = insert_event(sessions)
    address = client.get("/api/v1/me/feed/address").json()["url"]
    cid = register(client, scopes="calendar:read calendar:write")
    query, _ = authorize(client, cid, scopes="calendar:read calendar:write", audience=resource("extension"))
    credentials = exchange(client, cid, query["code"][0], resource("extension")).json()
    with sessions() as db:
        other = ensure_user(db, "other-owner")
        attach_link(
            db,
            other,
            db.get(Event, event_id),
            "https://www.youtube.com/watch?v=otheruser01",
            "Other owner",
            "preview",
        )
        db.add(
            CommandReceipt(
                id="fixture-receipt",
                owner_id="local-reviewer",
                operation="fixture",
                payload_hash="fixture",
                result={"private": "fixture"},
                expires_at=9999999999,
            )
        )
        job = Job(kind="projection", payload={"user_id": "local-reviewer"}, state="failed", attempts=5)
        successor = Job(kind="projection", payload={"user_id": "local-reviewer"})
        db.add_all([job, successor])
        db.flush()
        db.add(JobReplay(source_id=job.id, new_job_id=successor.id, reason="Fixture owner detail"))
        db.commit()
        original_revision = db.get(User, "local-reviewer").revision
    assert client.request("DELETE", "/api/v1/me", json={"confirmed": False}).status_code == 400
    response = client.request("DELETE", "/api/v1/me", json={"confirmed": True})
    assert response.status_code == 200 and response.json()["identity_cleanup"] == "not_applicable"
    assert (
        "anke_sports_session" in response.headers["set-cookie"]
        and "Max-Age=0" in response.headers["set-cookie"]
    )
    assert client.get(address).status_code == 404
    assert client.get("/api/v1/me/calendar").status_code == 401
    assert client.post("/api/v1/auth/local").status_code == 403
    headers = {"Authorization": "Bearer " + credentials["access_token"]}
    assert client.get("/api/v1/me/calendar", headers=headers).status_code == 401
    assert (
        client.post(
            "/token",
            data={
                "client_id": cid,
                "grant_type": "refresh_token",
                "refresh_token": credentials["refresh_token"],
                "resource": resource("extension"),
            },
        ).status_code
        == 400
    )
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        assert user.deleted and user.revision == original_revision + 1 and user.config == {}
        feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
        assert feed.revoked and feed.paused and not feed.body and not feed.token_ciphertext
        for cls in (
            CommandReceipt,
            OAuthGrant,
            OAuthRequest,
            OAuthTokenRecord,
            Session,
            VideoMatch,
            JobReplay,
        ):
            assert db.scalar(select(func.count()).select_from(cls)) == 0
        assert (
            db.scalar(select(func.count()).select_from(Projection).where(Projection.feed_id == feed.id)) == 0
        )
        assert all(link.owner_id == "other-owner" for link in db.scalars(select(Link)))
        assert db.get(User, "other-owner").deleted is False
        assert db.scalar(select(Feed).where(Feed.owner_id == "other-owner")).revoked is False
        assert db.get(Event, event_id) is not None
        assert all(j.payload.get("user_id") != "local-reviewer" for j in db.scalars(select(Job)))


@pytest.mark.parametrize("key", [None, "account-deleted-command"])
def test_stale_mcp_or_http_command_cannot_recreate_receipts_or_jobs(disk_stack, key):
    from sqlalchemy import func, select
    from app import actions
    from app.db import CommandReceipt, Job
    from app.service import enqueue

    sessions, _ = disk_stack
    with sessions() as stale:
        user = stale.get(User, "local-reviewer")
        with sessions() as other:
            delete_account_data(other, user.id)
            other.commit()
        called = []
        with pytest.raises(HTTPException):
            actions.command(stale, user, "fixture", key, {}, lambda: called.append(True))
        assert not called
        assert enqueue(stale, "projection", {"user_id": user.id}) is False
        stale.commit()
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(CommandReceipt)) == 0
        assert (
            db.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.payload["user_id"].as_string() == "local-reviewer")
            )
            == 0
        )


def test_erasure_failure_rolls_back_user_and_private_state(stack, monkeypatch):
    from sqlalchemy import select
    from app import privacy
    from app.db import Feed

    _, sessions = stack
    with sessions() as db:
        before = copy.deepcopy(db.get(User, "local-reviewer").config)
        feed = db.scalar(select(Feed))
        encrypted = feed.token_ciphertext

        def fail(*_):
            raise RuntimeError("simulated storage failure")

        monkeypatch.setattr(privacy, "delete_owner_connections", fail)
        with pytest.raises(RuntimeError):
            delete_account_data(db, "local-reviewer")
        db.rollback()
        db.refresh(feed)
        assert not db.get(User, "local-reviewer").deleted
        assert db.get(User, "local-reviewer").config == before
        assert not feed.revoked and feed.token_ciphertext == encrypted


def test_running_personal_job_cannot_publish_after_account_erasure(disk_stack, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event as Signal
    from sqlalchemy import select
    from app import worker
    from app.db import Feed, Job, Projection
    from tests.test_jobs import job

    sessions, _ = disk_stack
    ident = job(sessions, "projection", {"user_id": "local-reviewer"})
    started, release = Signal(), Signal()
    execute = worker.execute_claim

    def delayed(db, claim):
        db.get(User, "local-reviewer")  # Cache a real pre-deletion identity.
        started.set()
        assert release.wait(5)
        execute(db, claim)

    monkeypatch.setattr(worker, "execute_claim", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(worker.run_one, ident)
        assert started.wait(5)
        try:
            with sessions() as db:
                delete_account_data(db, "local-reviewer")
                db.commit()
        finally:
            release.set()
        assert running.result(timeout=5)
    with sessions() as db:
        assert db.get(Job, ident) is None
        assert db.scalar(select(Feed)).body == ""
        assert db.scalar(select(Projection)) is None


def test_queued_channel_fetches_stop_after_last_owner_deletion(stack, monkeypatch):
    from sqlalchemy import select
    from app import content, worker
    from app.db import Job, Video
    from tests.test_content import CHANNEL, VID, setup_content
    from tests.test_jobs import job

    _, sessions = stack
    setup_content(sessions)
    queued = [
        job(sessions, kind, {"channel_id": CHANNEL, "video_ids": [VID]})
        for kind in ("youtube_poll", "youtube_videos", "youtube_channel_metadata")
    ]
    with sessions() as db:
        delete_account_data(db, "local-reviewer")
        db.commit()
    calls = []
    monkeypatch.setattr(content, "youtube_request", lambda *args: calls.append(args))
    for ident in queued:
        assert worker.run_one(ident)
    assert calls == []
    with sessions() as db:
        assert db.get(Video, VID) is not None  # Public metadata follows its independent retention policy.
        assert all(db.get(Job, ident).state == "done" for ident in queued)
        assert not db.scalars(select(Job).where(Job.payload["user_id"].as_string() == "local-reviewer")).all()


def test_shared_content_continues_for_other_interested_owner(stack, monkeypatch):
    from sqlalchemy import select
    from app import content
    from app.db import Event, Link, Video
    from app.schemas import CreatorFollow
    from app.service import ensure_user
    from tests.test_content import CHANNEL, VID, api_video, setup_content

    _, sessions = stack
    event_id = setup_content(sessions)
    with sessions() as db:
        other = ensure_user(db, "remaining-owner")
        save_config(
            db,
            other,
            {
                **other.config,
                "follows": [{"type": "team", "source_key": "fixture:LAL"}],
                "creators": [
                    CreatorFollow(channel_id=CHANNEL, scope_keys=["fixture:LAL"]).model_dump()
                ],
                "event_overrides": [{"event_key": db.get(Event, event_id).source_key, "state": "include"}],
            },
            other.revision,
        )
        content.match_video(db, db.get(Video, VID))
        db.commit()
        title = db.get(Video, VID).title
        delete_account_data(db, "local-reviewer")
        db.commit()
    calls = []
    monkeypatch.setattr(
        content, "youtube_request", lambda *args: calls.append(args) or {"items": [api_video(title)]}
    )
    with sessions() as db:
        content.refresh_videos(db, CHANNEL, [VID])
        db.commit()
        assert calls and db.get(User, "remaining-owner").deleted is False
        assert all(link.owner_id == "remaining-owner" for link in db.scalars(select(Link)))


def test_firebase_cleanup_is_durable_target_bound_and_retryable(stack, monkeypatch):
    from types import SimpleNamespace
    from firebase_admin import auth
    from sqlalchemy import select
    from app import security, worker
    from app.config import settings
    from app.db import Job
    from app.jobs import replay_job

    client, sessions = stack
    monkeypatch.setattr(settings(), "firebase_project_id", "fixture-project")
    named_app = SimpleNamespace(project_id="fixture-project")
    monkeypatch.setattr(security, "firebase_app", lambda: named_app)
    monkeypatch.setattr(auth, "verify_id_token", lambda *args, **kwargs: {"uid": "local-reviewer"})
    response = client.request(
        "DELETE",
        "/api/v1/me",
        headers={"Authorization": "Bearer synthetic-firebase-token"},
        json={"confirmed": True},
    )
    assert response.status_code == 200 and response.json()["identity_cleanup"] == "queued"
    with sessions() as db:
        pending = db.scalar(select(Job).where(Job.kind == "identity_cleanup"))
        ident = pending.id
        assert pending.payload["firebase_project"] == "fixture-project"
    calls = []
    monkeypatch.setattr(auth, "delete_user", lambda uid, app: calls.append((uid, app.project_id)))
    monkeypatch.setattr(settings(), "firebase_project_id", "different-project")
    assert worker.run_one(ident)
    assert not calls
    with sessions() as db:
        pending = db.get(Job, ident)
        assert pending.error == "IDENTITY_TARGET_MISMATCH"
        pending.state = "failed"
        pending.attempts = 5
        db.commit()
        next_id = replay_job(db, ident, 5, "Fixture target restored", apply=True)["new_job_id"]
        db.commit()
    monkeypatch.setattr(settings(), "firebase_project_id", "fixture-project")
    assert worker.run_one(next_id)
    assert calls == [("local-reviewer", "fixture-project")]
    assert worker.run_one(next_id) is False


def test_identity_cleanup_accepts_already_deleted_remote_user(stack, monkeypatch):
    from firebase_admin import auth
    from app import security
    from app.config import settings
    from app.privacy import cleanup_identity

    _, sessions = stack
    monkeypatch.setattr(settings(), "firebase_project_id", "fixture-project")
    monkeypatch.setattr(security, "firebase_app", lambda: object())

    def absent(*args, **kwargs):
        raise auth.UserNotFoundError("Synthetic missing user")

    monkeypatch.setattr(auth, "delete_user", absent)
    with sessions() as db:
        delete_account_data(db, "local-reviewer", firebase_project="fixture-project")
        db.commit()
        cleanup_identity(db, {"user_id": "local-reviewer", "firebase_project": "fixture-project"})
        db.commit()


@pytest.mark.parametrize("exchange_kind", ["authorization_code", "refresh_token"])
def test_token_exchange_started_before_deletion_cannot_issue_credentials(
    disk_stack, monkeypatch, exchange_kind
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event as Signal
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app import oauth
    from app.db import get_db, OAuthGrant, OAuthTokenRecord
    from app.main import app
    from tests.test_oauth import register, authorize, exchange, VERIFIER, REDIRECT

    sessions, _ = disk_stack
    monkeypatch.setattr(oauth, "SessionLocal", sessions)

    def override():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override
    try:
        client = TestClient(app, headers={"Origin": "http://127.0.0.1:3000"})
        client.post("/api/v1/auth/local").raise_for_status()
        cid = register(client)
        query, _ = authorize(client, cid)
        if exchange_kind == "authorization_code":
            data = {
                "client_id": cid,
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "code_verifier": VERIFIER,
                "redirect_uri": REDIRECT,
                "resource": oauth.resource(),
            }
        else:
            tokens = exchange(client, cid, query["code"][0]).json()
            data = {
                "client_id": cid,
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "resource": oauth.resource(),
            }
        started, release = Signal(), Signal()
        original = oauth.lock_owner

        def paused(db, owner_id):
            started.set()
            assert release.wait(5)
            return original(db, owner_id)

        monkeypatch.setattr(oauth, "lock_owner", paused)
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(client.post, "/token", data=data)
            assert started.wait(5)
            try:
                with sessions() as db:
                    delete_account_data(db, "local-reviewer")
                    db.commit()
            finally:
                release.set()
            result = request.result(timeout=5)
            assert result.status_code == 400 and result.json()["error"] == "invalid_grant"
        with sessions() as db:
            assert db.scalar(select(OAuthGrant)) is None
            assert db.scalar(select(OAuthTokenRecord)) is None
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_http_failures_never_log_private_exception_details(stack, monkeypatch, caplog):
    from app import actions

    client, _ = stack
    secret = "synthetic-private-feed-token"

    def fail(*args, **kwargs):
        raise RuntimeError("https://fixture.invalid/feeds/" + secret + ".ics")

    monkeypatch.setattr(actions, "get_schedule", fail)
    result = client.get(
        "/api/v1/events", params={"from": "2026-09-01T00:00:00Z", "to": "2026-10-01T00:00:00Z"}
    )
    assert result.status_code == 503 and result.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert result.json()["error"]["request_id"] == result.headers["x-request-id"]
    assert secret not in result.text and secret not in caplog.text
    assert "REQUEST_FAILED" in caplog.text and "RuntimeError" in caplog.text
