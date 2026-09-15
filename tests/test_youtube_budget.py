import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event as Signal, Lock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app import db as database, jobs, oauth, providers, worker, youtube_budget as budget
from app.config import settings
from app.db import Creator, Job, Source, User, Video, YouTubeBudget, get_db
from app.main import app
from app.service import save_config

REAL_HTTP_CLIENT = httpx.Client
CHANNEL = "UC" + "a" * 22
SCOPE = "fixture:team"


@pytest.fixture
def quota_env(disk_stack, monkeypatch):
    sessions, _ = disk_stack
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(oauth, "SessionLocal", sessions)
    monkeypatch.setattr(settings(), "youtube_project_id", "anke-synthetic-quota")
    monkeypatch.setattr(settings(), "youtube_daily_budget", 6)
    monkeypatch.setenv("YOUTUBE_API_KEY", "synthetic-budget-key")

    def stub(handler):
        monkeypatch.setattr(
            providers.httpx,
            "Client",
            lambda **kwargs: REAL_HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
        )

    stub(lambda request: httpx.Response(200, json={"items": []}))
    yield sessions, stub


@pytest.fixture
def quota_client(quota_env):
    sessions, _ = quota_env

    def dependency():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    client = TestClient(app, headers={"Origin": "http://127.0.0.1:3000"})
    assert client.post("/api/v1/auth/local").status_code == 200
    with sessions() as db:
        db.add(
            Source(
                id=SCOPE,
                name="Fixture team",
                short_name="TEAM",
                sport="basketball",
                kind="team",
                color="#123456",
                provider="fixture",
                demo=True,
            )
        )
        user = db.get(User, "local-reviewer")
        save_config(
            db,
            user,
            {**user.config, "follows": [{"type": "team", "source_key": SCOPE}]},
            user.revision,
        )
        db.commit()
    yield client
    client.close()
    app.dependency_overrides.clear()


def snapshot(sessions):
    with sessions() as db:
        return budget.status(db)


def channels_response(request):
    assert request.url.path.endswith("/channels")
    return httpx.Response(
        200,
        json={
            "items": [
                {
                    "id": CHANNEL,
                    "snippet": {"title": "Synthetic channel"},
                    "contentDetails": {"relatedPlaylists": {"uploads": "UUfixture"}},
                }
            ]
        },
    )


def test_creator_requires_a_current_follow_before_any_youtube_request(quota_env, quota_client):
    sessions, stub = quota_env
    calls = []
    stub(lambda request: calls.append(request) or channels_response(request))
    revision = quota_client.get("/api/v1/me/calendar").json()["revision"]
    missing = quota_client.post(
        "/api/v1/me/creators", json={"url": CHANNEL, "expected_revision": revision}
    )
    unrelated = quota_client.post(
        "/api/v1/me/creators",
        json={"url": CHANNEL, "scope_keys": ["fixture:other"], "expected_revision": revision},
    )
    assert missing.status_code == 422
    assert unrelated.status_code == 409
    assert unrelated.json()["error"]["code"] == "CREATOR_SCOPE_NOT_FOLLOWED"
    assert calls == [] and snapshot(sessions)["reserved_units"] == 0


def test_independent_connections_share_cap_and_key_rotation_cannot_reset_it(quota_env, monkeypatch):
    sessions, stub = quota_env
    calls = []
    guard = Lock()
    barrier = Barrier(12)

    def request(req):
        with guard:
            calls.append(req.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={"items": []})

    stub(request)

    def run(i):
        barrier.wait(timeout=5)
        try:
            providers.youtube_request(["videos", "channels", "playlistItems"][i % 3], {})
            return True
        except HTTPException as exc:
            assert exc.detail["code"] == "YOUTUBE_BUDGET_EXHAUSTED"
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(run, range(12)))
    assert sum(results) == len(calls) == 6
    monkeypatch.setenv("YOUTUBE_API_KEY", "rotated-synthetic-budget-key")
    with pytest.raises(HTTPException):
        providers.youtube_request("videos", {})
    state = snapshot(sessions)
    assert state["reserved_units"] == 6 and state["available_units"] == 0 and len(calls) == 6
    assert "synthetic-budget-key" not in str(state) and "anke-synthetic-quota" not in str(state)


def test_failed_request_and_rolled_back_creator_write_keep_charge(quota_env, quota_client):
    sessions, stub = quota_env

    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    stub(timeout)
    with pytest.raises(HTTPException):
        providers.youtube_request("videos", {})
    assert snapshot(sessions)["reserved_units"] == 1
    stub(channels_response)
    failed = quota_client.post(
        "/api/v1/me/creators",
        json={"url": CHANNEL, "scope_keys": [SCOPE], "expected_revision": 99},
    )
    assert failed.status_code == 409
    with sessions() as db:
        assert db.get(Creator, CHANNEL) is None
        assert not db.get(User, "local-reviewer").config["creators"]
    assert snapshot(sessions)["reserved_units"] == 2


def test_creator_http_and_mcp_replay_does_not_spend_more_quota(quota_env, quota_client):
    from tests.test_mcp import connected, grant

    sessions, stub = quota_env
    stub(channels_response)
    response = quota_client.post("/api/v1/me/creators/resolve", json={"url": CHANNEL})
    assert response.status_code == 200
    token = grant(quota_client)
    revision = quota_client.get("/api/v1/me/calendar").json()["revision"]

    async def run():
        async with connected(token) as session:
            args = {
                "data": {"url": CHANNEL, "scope_keys": [SCOPE], "expected_revision": revision},
                "idempotency_key": "quota-creator-replay",
            }
            first = await session.call_tool("add_creator", args)
            assert not first.isError, first
            replay = await session.call_tool("add_creator", args)
            assert not replay.isError and replay.structuredContent == first.structuredContent

    asyncio.run(run())
    assert snapshot(sessions)["reserved_units"] == 2
    with sessions() as db:
        assert db.get(User, "local-reviewer").revision == revision + 1


@pytest.mark.parametrize(
    "instant,expected",
    [
        ("2026-03-08T08:01:00+00:00", "2026-03-09T07:00:00+00:00"),
        ("2026-11-01T07:01:00+00:00", "2026-11-02T08:00:00+00:00"),
    ],
)
def test_pacific_reset_handles_dst_and_budget_changes(quota_env, monkeypatch, instant, expected):
    sessions, _ = quota_env
    current = datetime.fromisoformat(instant)
    monkeypatch.setattr(budget, "clock", lambda: current)
    monkeypatch.setattr(settings(), "youtube_daily_budget", 2)
    providers.youtube_request("videos", {})
    monkeypatch.setattr(settings(), "youtube_daily_budget", 1)
    with pytest.raises(HTTPException):
        providers.youtube_request("channels", {})
    monkeypatch.setattr(settings(), "youtube_daily_budget", 100)
    state = snapshot(sessions)
    assert state["daily_limit"] == 1 and state["reset_at"] == expected
    current = datetime.fromisoformat(expected) + timedelta(seconds=1)
    providers.youtube_request("playlistItems", {})
    state = snapshot(sessions)
    assert state["daily_limit"] == 100 and state["reserved_units"] == 1 and state["state"] == "available"


def test_upstream_quota_is_global_and_late_success_cannot_clear_it(quota_env):
    sessions, stub = quota_env
    entered, release = Signal(), Signal()

    def response(request):
        if request.url.params.get("id") == "slow":
            entered.set()
            assert release.wait(5)
            return httpx.Response(200, json={"items": []})
        return httpx.Response(
            403, json={"error": {"errors": [{"reason": "quotaExceeded"}], "message": "private upstream text"}}
        )

    stub(response)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(providers.youtube_request, "videos", {"id": "slow"})
        assert entered.wait(5)
        try:
            with pytest.raises(HTTPException) as error:
                providers.youtube_request("channels", {})
            assert error.value.detail["code"] == "YOUTUBE_QUOTA_EXHAUSTED"
            assert "private upstream text" not in str(error.value.detail)
        finally:
            release.set()
        assert first.result(timeout=5) == {"items": []}
    assert snapshot(sessions)["state"] == "waiting"
    with pytest.raises(HTTPException):
        providers.youtube_request("playlistItems", {})
    assert snapshot(sessions)["reserved_units"] == 2


def test_rate_limit_html_preserves_retry_after_across_midnight(quota_env, monkeypatch):
    sessions, stub = quota_env
    current = datetime(2026, 9, 10, 6, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(budget, "clock", lambda: current)
    stub(lambda request: httpx.Response(429, text="private rate response", headers={"Retry-After": "3600"}))
    with pytest.raises(HTTPException) as error:
        providers.youtube_request("videos", {})
    assert error.value.detail["code"] == "YOUTUBE_RATE_LIMITED"
    current += timedelta(minutes=2)
    state = snapshot(sessions)
    assert state["reserved_units"] == 0 and state["state"] == "waiting"
    assert state["resume_at"] == "2026-09-10T07:59:00+00:00"
    current += timedelta(hours=1)
    stub(lambda request: httpx.Response(200, json={"items": []}))
    providers.youtube_request("videos", {})
    assert snapshot(sessions)["reserved_units"] == 1


def test_late_quota_response_from_previous_day_cannot_block_new_day(quota_env, monkeypatch):
    sessions, _ = quota_env
    current = datetime(2026, 9, 10, 6, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(budget, "clock", lambda: current)
    ticket = budget.reserve("videos")
    current += timedelta(minutes=2)
    budget.reserve("channels")
    budget.upstream_wait(ticket, "YOUTUBE_QUOTA_EXHAUSTED")
    assert snapshot(sessions)["state"] == "available" and snapshot(sessions)["reserved_units"] == 1


def test_waits_preserve_monotonic_claims_without_exhausting_failure_budget(quota_env, monkeypatch):
    sessions, _ = quota_env
    current = datetime.now(timezone.utc)
    monkeypatch.setattr(jobs, "now", lambda: current.isoformat())
    monkeypatch.setattr(budget, "clock", lambda: current)
    with sessions() as db:
        db.execute(delete(Job))
        db.add(
            Job(
                kind="youtube_videos",
                payload={"channel_id": CHANNEL, "video_ids": ["abcdefghijk"]},
                due_at=current.isoformat(),
            )
        )
        db.commit()
        ident = db.scalar(select(Job.id))
    claims = []
    for _ in range(7):
        with sessions() as db:
            claim, _ = jobs.claim_job(db, ident)
            assert claim
            db.commit()
            claims.append(claim)
            jobs.fail_job(
                db, claim, budget.wait_error("YOUTUBE_BUDGET_EXHAUSTED", current + timedelta(seconds=60))
            )
            db.commit()
            row = db.get(Job, ident)
            assert row.state == "pending"
            current = datetime.fromisoformat(row.due_at) + timedelta(seconds=1)
    assert [c.attempt for c in claims] == list(range(1, 8))
    with sessions() as db:
        with pytest.raises(jobs.LeaseLost):
            jobs.complete_job(db, claims[0])
        db.rollback()
        row = db.get(Job, ident)
        assert row.quota_waits == 7 and row.attempts == 7
    for count in range(5):
        with sessions() as db:
            claim, _ = jobs.claim_job(db, ident)
            assert claim
            db.commit()
            jobs.fail_job(db, claim, ValueError("REAL_FAILURE"))
            db.commit()
            row = db.get(Job, ident)
            assert (row.state == "failed") == (count == 4)
            current = datetime.fromisoformat(row.due_at) + timedelta(seconds=1)


def test_uploads_and_video_fetch_use_separate_budgeted_jobs(quota_env, monkeypatch):
    from tests.test_content import setup_content, api_video, VID

    sessions, stub = quota_env
    setup_content(sessions)
    current = datetime.now(timezone.utc)
    monkeypatch.setattr(budget, "clock", lambda: current)
    monkeypatch.setattr(jobs, "now", lambda: current.isoformat())
    monkeypatch.setattr(settings(), "youtube_daily_budget", 1)
    calls = []

    def response(request):
        endpoint = request.url.path.rsplit("/", 1)[-1]
        calls.append(endpoint)
        if endpoint == "playlistItems":
            return httpx.Response(
                200,
                json={
                    "items": [{"contentDetails": {"videoId": VID, "videoPublishedAt": current.isoformat()}}]
                },
            )
        if endpoint == "videos":
            return httpx.Response(200, json={"items": [api_video("Lakers Warriors preview")]})
        return channels_response(request)

    stub(response)
    with sessions() as db:
        db.execute(delete(Job))
        db.add(Job(kind="youtube_poll", payload={"channel_id": CHANNEL}, due_at=current.isoformat()))
        db.commit()
        ident = db.scalar(select(Job.id))
    assert worker.run_one(ident)
    with sessions() as db:
        assert db.get(Job, ident).state == "done"
        child = db.scalar(select(Job).where(Job.kind == "youtube_videos"))
        child_id = child.id
        current = datetime.fromisoformat(child.due_at) + timedelta(seconds=1)
        assert worker.run_one(child_id)
        # Observe the other transaction after ending this MySQL repeatable-read snapshot.
        db.rollback()
        child = db.get(Job, child_id)
        assert child.state == "pending" and child.attempts == 0 and calls == ["playlistItems"]
        current = datetime.fromisoformat(child.due_at) + timedelta(seconds=1)
    assert worker.run_one(child_id)
    assert calls == ["playlistItems", "videos"]
    with sessions() as db:
        assert db.get(Job, child_id).state == "done" and db.get(Video, VID).available


def test_missing_project_and_unbudgeted_endpoint_never_call_upstream(quota_env, monkeypatch):
    sessions, stub = quota_env
    calls = []
    stub(lambda request: calls.append(request))
    monkeypatch.setattr(settings(), "youtube_project_id", "")
    with pytest.raises(HTTPException) as error:
        providers.youtube_request("videos", {})
    assert error.value.detail["code"] == "YOUTUBE_PROJECT_REQUIRED"
    monkeypatch.setattr(settings(), "youtube_project_id", "anke-synthetic-quota")
    with pytest.raises(ValueError, match="YOUTUBE_ENDPOINT_NOT_BUDGETED"):
        providers.youtube_request("activities", {})
    assert not calls
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(YouTubeBudget)) == 0


@pytest.mark.parametrize("change", ["delete", "revision"])
def test_creator_preparation_rechecks_owner_after_network(quota_env, quota_client, change):
    from app.privacy import delete_account_data
    from app.service import save_config

    sessions, stub = quota_env
    revision = quota_client.get("/api/v1/me/calendar").json()["revision"]

    def response(request):
        # An independent connection can commit while the request is in flight.
        with sessions() as db:
            if change == "delete":
                delete_account_data(db, "local-reviewer")
            else:
                owner = db.get(User, "local-reviewer")
                save_config(db, owner, owner.config, revision)
            db.commit()
        return channels_response(request)

    stub(response)
    result = quota_client.post(
        "/api/v1/me/creators",
        json={"url": CHANNEL, "scope_keys": [SCOPE], "expected_revision": revision},
    )
    assert result.status_code == (403 if change == "delete" else 409)
    with sessions() as db:
        assert db.get(Creator, CHANNEL) is None
    assert snapshot(sessions)["reserved_units"] == 1


def test_quota_status_is_sanitized_and_403_retry_after_is_retained(quota_env, quota_client):
    sessions, stub = quota_env
    stub(
        lambda request: httpx.Response(
            403,
            headers={"Retry-After": "7200"},
            json={"error": {"errors": [{"reason": "rateLimitExceeded"}]}},
        )
    )
    with pytest.raises(HTTPException) as error:
        providers.youtube_request("channels", {})
    assert error.value.detail["retry_after_seconds"] >= 7200
    state = quota_client.get("/api/v1/status")
    assert state.status_code == 200
    body = state.json()["youtube_budget"]
    assert body["state"] == "waiting" and body["reserved_units"] == 1
    assert "anke-synthetic-quota" not in state.text and "synthetic-budget-key" not in state.text
    assert body == snapshot(sessions)


def test_parallel_creator_commands_spend_for_each_request_but_commit_once(quota_env, quota_client):
    from app.db import CommandReceipt

    sessions, stub = quota_env
    revision = quota_client.get("/api/v1/me/calendar").json()["revision"]
    barrier = Barrier(2)

    def response(request):
        barrier.wait(timeout=5)
        return channels_response(request)

    stub(response)

    def add(_):
        return quota_client.post(
            "/api/v1/me/creators",
            headers={"Idempotency-Key": "parallel-quota-command"},
            json={"url": CHANNEL, "scope_keys": [SCOPE], "expected_revision": revision},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(add, range(2))
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert snapshot(sessions)["reserved_units"] == 2
    with sessions() as db:
        assert db.get(User, "local-reviewer").revision == revision + 1
        assert db.scalar(select(func.count()).select_from(CommandReceipt)) == 1
