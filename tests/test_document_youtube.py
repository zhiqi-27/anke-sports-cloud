from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import subprocess
import sys
from threading import Barrier, Event, Lock

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from app.config import settings
from app.document_api import create_app
from app.document_runtime import Runtime
from app.document_store import LocalDocumentStore, StoreError, Write, clean
from app import document_youtube_budget as budgets

HTTP_CLIENT = httpx.Client
CHANNEL = "UC" + "a" * 22
OTHER = "UC" + "b" * 22
VIDEO = "abcdefghijk"
SECRET = "synthetic-document-youtube-key"


def channel_response(request):
    assert request.headers["x-goog-api-key"] == SECRET
    assert "key" not in request.url.params
    assert request.url.path.endswith("/channels")
    return httpx.Response(
        200,
        json={
            "items": [
                {
                    "id": CHANNEL,
                    "snippet": {"title": "合成创作者"},
                    "contentDetails": {"relatedPlaylists": {"uploads": "UUfixture"}},
                }
            ]
        },
    )


@pytest.fixture
def youtube(tmp_path, monkeypatch):
    store = LocalDocumentStore(tmp_path / "youtube.db")
    cfg = settings().model_copy(
        update={
            "storage_backend": "documents-local",
            "document_local_path": str(store.path),
            "youtube_project_id": "synthetic-document-project",
            "youtube_daily_budget": 64,
        }
    )
    monkeypatch.setenv("YOUTUBE_API_KEY", SECRET)
    runtime = Runtime(store, cfg)

    def stub(handler):
        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
        )

    stub(channel_response)
    yield runtime, stub
    store.close()


def test_many_connections_share_budget_and_key_rotation_does_not_reset(youtube, monkeypatch):
    rt, stub = youtube
    rt.cfg.youtube_daily_budget = 6
    gate, lock, calls = Barrier(12), Lock(), []

    def response(request):
        with lock:
            calls.append(request.url.path)
        return httpx.Response(200, json={"items": []})

    stub(response)

    def run(i):
        store = LocalDocumentStore(rt.store.path)
        other = Runtime(store, rt.cfg)
        gate.wait(timeout=10)
        try:
            other.youtube_request(["channels", "videos", "playlistItems"][i % 3], {})
            return True
        except HTTPException as exc:
            assert exc.detail["code"] == "YOUTUBE_BUDGET_EXHAUSTED"
            return False
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(run, range(12)))
    assert sum(results) == len(calls) == 6
    monkeypatch.setenv("YOUTUBE_API_KEY", "rotated-synthetic-key")
    with pytest.raises(HTTPException, match="YOUTUBE_BUDGET_EXHAUSTED"):
        rt.youtube_request("videos", {})
    state = rt.youtube_budget.status()
    assert state["reserved_units"] == 6 and state["available_units"] == 0
    assert SECRET not in json.dumps(state) and rt.cfg.youtube_project_id not in json.dumps(state)
    # Reservations use indexes, so they cannot create a state Change Feed loop.
    assert rt.store.changes("state")[0] == []


@pytest.mark.parametrize(
    "instant,reset",
    [
        ("2026-03-08T08:01:00+00:00", "2026-03-09T07:00:00+00:00"),
        ("2026-11-01T07:01:00+00:00", "2026-11-02T08:00:00+00:00"),
    ],
)
def test_dst_lower_cap_and_next_day_increase(youtube, monkeypatch, instant, reset):
    rt, _ = youtube
    current = datetime.fromisoformat(instant)
    monkeypatch.setattr(budgets, "clock", lambda: current)
    rt.cfg.youtube_daily_budget = 2
    rt.youtube_request("channels", {})
    rt.cfg.youtube_daily_budget = 1
    with pytest.raises(HTTPException):
        rt.youtube_request("channels", {})
    rt.cfg.youtube_daily_budget = 100
    assert rt.youtube_budget.status()["daily_limit"] == 1
    assert rt.youtube_budget.status()["reset_at"] == reset
    current = datetime.fromisoformat(reset) + timedelta(seconds=1)
    rt.youtube_request("channels", {})
    assert rt.youtube_budget.status()["reserved_units"] == 1
    assert rt.youtube_budget.status()["daily_limit"] == 100
    current -= timedelta(days=2)
    with pytest.raises(StoreError, match="YOUTUBE_CLOCK_MOVED_BACKWARD"):
        rt.youtube_request("channels", {})


def test_reservation_failure_and_uncertain_commit_never_send_http(youtube, monkeypatch):
    rt, stub = youtube
    calls = []
    stub(lambda request: calls.append(request) or httpx.Response(200, json={"items": []}))
    commit = rt.youtube_budget.commit

    def fail(old, value):
        raise StoreError("SYNTHETIC_STORAGE_DOWN", retryable=True)

    monkeypatch.setattr(rt.youtube_budget, "commit", fail)
    with pytest.raises(StoreError):
        rt.youtube_request("channels", {})
    assert not calls and rt.youtube_budget.status()["reserved_units"] == 0

    def uncertain(old, value):
        commit(old, value)
        raise StoreError("SYNTHETIC_COMMIT_REPLY_LOST", retryable=True)

    monkeypatch.setattr(rt.youtube_budget, "commit", uncertain)
    with pytest.raises(StoreError):
        rt.youtube_request("channels", {})
    assert not calls and rt.youtube_budget.status()["reserved_units"] == 1
    monkeypatch.setattr(rt.youtube_budget, "commit", commit)
    rt.youtube_request("channels", {})
    assert len(calls) == 1 and rt.youtube_budget.status()["reserved_units"] == 2


def test_late_success_cannot_clear_project_quota_and_long_wait_is_monotonic(youtube):
    rt, stub = youtube
    entered, release = Event(), Event()

    def response(request):
        if request.url.params.get("id") == "slow":
            entered.set()
            assert release.wait(10)
            return httpx.Response(200, json={"items": []})
        return httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})

    stub(response)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rt.youtube_request, "videos", {"id": "slow"})
        assert entered.wait(10)
        try:
            with pytest.raises(HTTPException, match="YOUTUBE_QUOTA_EXHAUSTED"):
                rt.youtube_request("channels", {})
        finally:
            release.set()
        assert future.result(timeout=10) == {"items": []}
    assert rt.youtube_budget.status()["state"] == "waiting"
    assert rt.youtube_budget.status()["reserved_units"] == 2


def test_rate_wait_crosses_midnight_and_old_quota_response_does_not_block_new_day(youtube, monkeypatch):
    rt, stub = youtube
    current = datetime(2026, 9, 10, 6, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(budgets, "clock", lambda: current)
    old_ticket = rt.youtube_budget.reserve("videos")
    stub(lambda request: httpx.Response(429, text="private upstream body", headers={"Retry-After": "3600"}))
    with pytest.raises(HTTPException, match="YOUTUBE_RATE_LIMITED"):
        rt.youtube_request("channels", {})
    current += timedelta(minutes=2)
    state = rt.youtube_budget.status()
    assert state["reserved_units"] == 0 and state["resume_at"] == "2026-09-10T07:59:00+00:00"
    rt.youtube_budget.upstream_wait(old_ticket, "YOUTUBE_QUOTA_EXHAUSTED")
    assert rt.youtube_budget.status() == state
    current += timedelta(hours=1)
    stub(channel_response)
    rt.youtube_request("channels", {})
    assert rt.youtube_budget.status()["state"] == "available"
    ticket = rt.youtube_budget.reserve("channels")
    rt.youtube_budget.upstream_wait(ticket, "YOUTUBE_RATE_LIMITED", 7200)
    resume = rt.youtube_budget.status()["resume_at"]
    error = rt.youtube_budget.upstream_wait(ticket, "YOUTUBE_RATE_LIMITED", 60)
    assert rt.youtube_budget.status()["resume_at"] == error.detail["resume_at"] == resume


def test_missing_config_bad_endpoint_and_corrupt_budget_fail_closed(youtube, monkeypatch):
    rt, _ = youtube
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    assert rt.youtube_budget.status()["state"] == "unconfigured"
    with pytest.raises(HTTPException, match="YOUTUBE_KEY_REQUIRED"):
        rt.youtube_request("channels", {})
    monkeypatch.setenv("YOUTUBE_API_KEY", SECRET)
    rt.cfg.youtube_project_id = ""
    with pytest.raises(HTTPException, match="YOUTUBE_PROJECT_REQUIRED"):
        rt.youtube_request("channels", {})
    rt.cfg.youtube_project_id = "synthetic-document-project"
    with pytest.raises(ValueError, match="YOUTUBE_ENDPOINT_NOT_BUDGETED"):
        rt.youtube_request("../channels", {})
    rt.youtube_request("channels", {})
    old = rt.youtube_budget.read()
    bad = clean(old)
    bad["payload"]["reserved_units"] = -1
    rt.store.batch("indexes", old["pk"], [Write("replace", "daily", bad, old["_etag"])])
    with pytest.raises(StoreError, match="YOUTUBE_BUDGET_INVALID"):
        rt.youtube_request("channels", {})


def test_channel_resolve_forms_and_public_video_identity(youtube):
    rt, stub = youtube
    calls = []

    def response(request):
        calls.append(request)
        if request.url.path.endswith("/videos"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": VIDEO,
                            "snippet": {"channelId": CHANNEL},
                            "status": {"privacyStatus": "public"},
                        }
                    ]
                },
            )
        return channel_response(request)

    stub(response)
    for value in [
        CHANNEL,
        "@示例频道",
        "https://youtube.com/@example/videos",
        "https://www.youtube.com/channel/" + CHANNEL,
        "https://youtu.be/" + VIDEO,
        "https://youtube.com/shorts/" + VIDEO,
    ]:
        assert rt.resolve_creator(value)["channel_id"] == CHANNEL
    assert len(calls) == rt.youtube_budget.status()["reserved_units"] == 8
    assert all(SECRET not in str(request.url) for request in calls)


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/@example",
        "http://youtube.com/@example",
        "https://youtube.com:444/@example",
        "https://user:pass@youtube.com/@example",
        "@example/evil",
        "https://youtube.com/channel/not-a-channel",
        "https://youtube.com/channel/",
        "https://youtube.com/\\@example",
        "@example\nprivate",
    ],
)
def test_invalid_channel_does_not_spend_quota(youtube, value):
    rt, _ = youtube
    with pytest.raises(HTTPException):
        rt.resolve_creator(value)
    assert rt.youtube_budget.status()["reserved_units"] == 0


@pytest.mark.parametrize(
    "payload,code",
    [
        ({}, "INVALID_YOUTUBE_RESPONSE"),
        ({"items": []}, "CHANNEL_NOT_FOUND"),
        ({"items": [None]}, "INVALID_YOUTUBE_RESPONSE"),
        ({"items": [{"id": CHANNEL}]}, "INVALID_YOUTUBE_RESPONSE"),
        (
            {
                "items": [
                    {
                        "id": OTHER,
                        "snippet": {"title": "wrong"},
                        "contentDetails": {"relatedPlaylists": {"uploads": "UUwrong"}},
                    }
                ]
            },
            "INVALID_YOUTUBE_RESPONSE",
        ),
    ],
)
def test_incomplete_or_wrong_channel_is_not_accepted(youtube, payload, code):
    rt, stub = youtube
    stub(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(HTTPException, match=code):
        rt.resolve_creator(CHANNEL)
    assert rt.youtube_budget.status()["reserved_units"] == 1


def test_private_or_wrong_video_cannot_resolve_a_creator(youtube):
    rt, stub = youtube
    for row in [
        {"id": VIDEO, "snippet": {"channelId": CHANNEL}, "status": {"privacyStatus": "private"}},
        {"id": "xxxxxxxxxxx", "snippet": {"channelId": CHANNEL}, "status": {"privacyStatus": "public"}},
    ]:
        stub(lambda request: httpx.Response(200, json={"items": [row]}))
        with pytest.raises(HTTPException):
            rt.resolve_creator("https://youtu.be/" + VIDEO)
    assert rt.youtube_budget.status()["reserved_units"] == 2


def test_http_resolution_auth_origin_read_only_and_safe_transport_errors(youtube, caplog):
    rt, stub = youtube
    with TestClient(create_app(rt.store, rt.cfg), headers={"Origin": rt.cfg.web_url}) as client:
        path = "/api/v1/me/creators/resolve"
        assert client.post(path, json={"url": CHANNEL}).status_code == 401
        assert rt.youtube_budget.status()["reserved_units"] == 0
        assert client.post("/api/v1/auth/local").status_code == 200
        account = rt.accounts.active("local-reviewer")
        with caplog.at_level(logging.DEBUG):
            response = client.post(path, json={"url": CHANNEL})
        assert response.status_code == 200 and response.json()["name"] == "合成创作者"
        assert "uploads_id" not in response.json()
        assert rt.accounts.active("local-reviewer") == account
        assert (
            client.post(path, json={"url": CHANNEL}, headers={"Origin": "https://bad.example"}).status_code
            == 403
        )
        assert rt.youtube_budget.status()["reserved_units"] == 1
        stub(lambda request: httpx.Response(403, json={"error": {"message": "private-provider-error"}}))
        with caplog.at_level(logging.DEBUG):
            response = client.post(path, json={"url": CHANNEL})
        assert response.status_code == 503 and response.json()["error"]["code"] == "YOUTUBE_API_UNAVAILABLE"
        assert SECRET not in caplog.text and "private-provider-error" not in caplog.text + response.text
        status = client.get("/api/v1/status").json()
        assert status["youtube_budget"]["reserved_units"] == 2
        assert status["integrations"]["youtube_discovery"] == "not_migrated"
        assert not rt.accounts.active("local-reviewer")["payload"]["config"]["creators"]


def test_fresh_process_retains_budget_and_document_runtime_has_no_sql(youtube):
    rt, _ = youtube
    rt.youtube_request("channels", {})
    env = {
        **os.environ,
        "ANKE_SPORTS_STORAGE_BACKEND": "documents-local",
        "ANKE_SPORTS_DOCUMENT_LOCAL_PATH": str(rt.store.path),
        "ANKE_SPORTS_YOUTUBE_PROJECT_ID": rt.cfg.youtube_project_id,
        "ANKE_SPORTS_YOUTUBE_DAILY_BUDGET": "64",
        "YOUTUBE_API_KEY": SECRET,
    }
    code = """
import sys
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    result = client.get('/api/v1/status')
    assert result.status_code == 200
    assert result.json()['youtube_budget']['reserved_units'] == 1
assert 'app.db' not in sys.modules and 'app.sql_app' not in sys.modules
print('Persisted reservation retained; SQL runtime absent')
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "SQL runtime absent" in result.stdout
