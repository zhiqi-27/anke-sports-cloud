from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient
from icalendar import Calendar
import pytest

from app.config import settings
from app.document_accounts import owner_partition
from app.document_api import create_app
from app.document_runtime import Runtime
from app.document_store import Conflict, LocalDocumentStore, StoreError, Write, clean, partition_items
from app.document_worker import LocalQueue, dispatch, run_job
from tests.test_document_feeds import event


def samples():
    rows = []
    for index in range(3):
        row = vars(event(index=index))
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        rows.append(row)
    sources = [
        {
            "id": "fixture:league",
            "name": "演示联赛",
            "short_name": "演示",
            "color": "#23804d",
            "sport": "basketball",
            "kind": "competition",
            "demo": True,
        }
    ]
    return rows, sources


def drain(runtime):
    queue = LocalQueue(runtime.store)
    for _ in range(200):
        advanced = dispatch(runtime.store, queue.send)
        consumed = queue.consume(runtime)
        if not advanced and not consumed:
            return
    raise AssertionError("Background processing did not become idle")


@pytest.fixture
def document_stack(tmp_path):
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


def follow(client):
    user = client.get("/api/v1/me/calendar").json()
    data = {
        "expected_revision": user["revision"],
        "follows": [{"type": "competition", "source_key": "fixture:league"}],
    }
    preview = client.post("/api/v1/me/follows/preview", json=data)
    assert preview.status_code == 200, preview.text
    data["confirmation"] = preview.json()["confirmation"]
    saved = client.put(
        "/api/v1/me/follows", json=data, headers={"Idempotency-Key": "document-follow-command"}
    )
    assert saved.status_code == 200, saved.text
    return data, saved.json()


def ics_rows(response):
    assert response.status_code == 200, response.text
    return {str(row["UID"]): row for row in Calendar.from_ical(response.content).walk("VEVENT")}


def test_http_follow_worker_feed_rotation_and_exact_idempotent_response(document_stack):
    client, runtime, rows, sources = document_stack
    assert client.get("/api/v1/me/calendar").status_code == 401
    assert client.get("/api/v1/status").status_code == 200
    assert client.get("/api/v1/sources?dataset=demo").json()["items"] == sources
    assert client.post("/api/v1/auth/local").status_code == 200
    drain(runtime)
    data, result = follow(client)
    assert result["feed"]["status"] == "updating"
    drain(runtime)
    address = client.get("/api/v1/me/feed/address").json()["url"]
    first = client.get(address)
    initial = ics_rows(first)
    assert len(initial) == 3
    before = list(partition_items(runtime.store, "state", owner_partition("local-reviewer"), "outbox"))
    replay = client.put(
        "/api/v1/me/follows", json=data, headers={"Idempotency-Key": "document-follow-command"}
    )
    assert replay.status_code == 200 and replay.json() == result
    assert (
        list(partition_items(runtime.store, "state", owner_partition("local-reviewer"), "outbox")) == before
    )
    assert client.get(address, headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    assert client.head(address).content == b""
    assert client.post("/api/v1/me/feed/rotate", json={"confirmed": True}).json()["uid_unchanged"]
    assert client.get(address).status_code == 404
    address = client.get("/api/v1/me/feed/address").json()["url"]
    assert client.get(address).content == first.content
    rows[0]["starts_at"] = (datetime.fromisoformat(rows[0]["starts_at"]) + timedelta(hours=3)).isoformat()
    runtime.catalog.publish("fixture", rows, sources, expected_revision=1, complete=True)
    drain(runtime)
    changed = ics_rows(client.get(address))
    assert changed.keys() == initial.keys()
    assert sorted(int(changed[uid]["SEQUENCE"]) - int(initial[uid]["SEQUENCE"]) for uid in initial) == [
        0,
        0,
        1,
    ]
    state = runtime.accounts.active("local-reviewer")
    for _ in range(3):
        client.get(address).raise_for_status()
    assert runtime.accounts.active("local-reviewer") == state  # Feed reads never enqueue or mutate.
    assert client.get("/api/v1/me/config/export").json() == result["config"]
    assert (
        client.get("/api/v1/me/connections").json()["error"]["code"] == "DOCUMENT_FEATURE_UNAVAILABLE"
    )
    assert client.post("/api/v1/auth/logout").json()["signed_out"]
    assert client.get("/api/v1/me/calendar").status_code == 401


def test_preview_staleness_cursors_pause_and_current_scope(document_stack):
    client, runtime, rows, sources = document_stack
    client.post("/api/v1/auth/local").raise_for_status()
    drain(runtime)
    params = {
        "from": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        "to": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        "dataset": "demo",
        "limit": 1,
    }
    page = client.get("/api/v1/events", params=params)
    assert page.status_code == 200 and len(page.json()["items"]) == 1
    cursor = page.json()["next_cursor"]
    assert cursor and client.get("/api/v1/events", params={**params, "cursor": cursor}).status_code == 200
    data = {"expected_revision": 0, "follows": [{"type": "competition", "source_key": "fixture:league"}]}
    preview = client.post("/api/v1/me/follows/preview", json=data).json()
    rows[0]["starts_at"] = (datetime.fromisoformat(rows[0]["starts_at"]) + timedelta(hours=1)).isoformat()
    rows[0]["updated_at"] = datetime.now(timezone.utc).isoformat()
    runtime.catalog.publish("fixture", rows, sources, expected_revision=1, complete=True)
    assert client.get("/api/v1/events", params={**params, "cursor": cursor}).status_code == 409
    stale = client.put("/api/v1/me/follows", json={**data, "confirmation": preview["confirmation"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "FOLLOWS_PREVIEW_CHANGED"
    assert runtime.accounts.active("local-reviewer")["payload"]["revision"] == 0
    drain(runtime)
    follow(client)
    drain(runtime)
    address = client.get("/api/v1/me/feed/address").json()["url"]
    original = client.get(address)
    client.post("/api/v1/me/feed/pause", json={"confirmed": True}).raise_for_status()
    rows[0]["venue"] = "演示改期场馆"
    runtime.catalog.publish("fixture", rows, sources, expected_revision=2, complete=True)
    drain(runtime)
    assert client.get(address).content == original.content
    client.post("/api/v1/me/feed/pause", json={"confirmed": False}).raise_for_status()
    drain(runtime)
    assert client.get(address).content != original.content


def test_provider_incomplete_batches_and_prepared_but_unpublished_blocks_stay_hidden(
    document_stack, monkeypatch
):
    _, runtime, rows, sources = document_stack
    old = runtime.catalog.capture()
    original = [vars(row) for row in old.events()]
    with pytest.raises(StoreError, match="INCOMPLETE"):
        runtime.catalog.publish("fixture", [], [], expected_revision=1, complete=False)
    assert (
        runtime.catalog.publish("fixture", [], [], expected_revision=1, complete=True)["payload"]["revision"]
        == 1
    )
    changed = deepcopy(rows)
    changed[0]["venue"] = "prepared only"
    batch = runtime.store.batch

    def break_pointer(container, pk, writes):
        if pk == "provider:fixture" and any(write.id == "schedule" for write in writes):
            raise StoreError("SYNTHETIC_OUTAGE", retryable=True)
        return batch(container, pk, writes)

    monkeypatch.setattr(runtime.store, "batch", break_pointer)
    with pytest.raises(StoreError, match="OUTAGE"):
        runtime.catalog.publish("fixture", changed, sources, expected_revision=1, complete=True)
    assert [vars(row) for row in runtime.catalog.capture().events()] == original
    monkeypatch.setattr(runtime.store, "batch", batch)
    runtime.catalog.publish("fixture", changed, sources, expected_revision=1, complete=True)
    assert [vars(row) for row in old.events()] == original  # Captured roots are immutable.
    with pytest.raises(StoreError, match="CHANGED"):
        old.assert_current()
    with pytest.raises(Conflict):
        runtime.catalog.publish("fixture", rows, sources, expected_revision=1, complete=True)
    changed[0]["id"] = "new-id-for-same-game"
    with pytest.raises(StoreError, match="IDENTITY_CONFLICT"):
        runtime.catalog.publish("fixture", changed, sources, expected_revision=2, complete=True)
    another = [{**row, "provider": "other"} for row in rows]
    with pytest.raises(StoreError, match="IDENTITY_CONFLICT"):
        runtime.catalog.publish("other", another, sources, expected_revision=0, complete=True)


def test_dispatch_send_failure_replays_without_losing_work_and_checkpoint_is_not_observed(document_stack):
    _, runtime, _, _ = document_stack
    runtime.accounts.ensure("queue-owner")
    delivered = []

    def interrupted(body, delay):
        delivered.append(json.loads(body))
        raise StoreError("SEND_RESPONSE_LOST", retryable=True)

    with pytest.raises(StoreError, match="RESPONSE_LOST"):
        dispatch(runtime.store, interrupted)
    checkpoint = runtime.store.get("indexes", "dispatch", "outbox")
    assert checkpoint["payload"]["cursor"] is None
    queue = LocalQueue(runtime.store)
    drain(runtime)
    assert all(
        runtime.store.get("state", item["pk"], item["job_id"])["state"] == "done" for item in delivered
    )
    cursor = runtime.store.get("indexes", "dispatch", "outbox")["payload"]["cursor"]
    assert not dispatch(runtime.store, queue.send)
    assert runtime.store.get("indexes", "dispatch", "outbox")["payload"]["cursor"] == cursor
    assert runtime.store.changes("state", cursor) == ([], cursor)


def test_expired_worker_cannot_commit_and_retry_survives_a_fresh_local_connection(
    document_stack, monkeypatch
):
    _, runtime, _, _ = document_stack
    runtime.accounts.ensure("retry-owner")
    row = next(partition_items(runtime.store, "state", owner_partition("retry-owner"), "outbox"))
    message = {"version": 1, "pk": row["pk"], "job_id": row["id"]}
    original = runtime.publish
    monkeypatch.setattr(
        runtime,
        "publish",
        lambda _: (_ for _ in ()).throw(StoreError("DOCUMENT_THROTTLED", retryable=True, retry_after=30)),
    )
    assert run_job(runtime, message)
    job = runtime.store.get("state", row["pk"], row["id"])
    assert job["state"] == "pending" and job["payload"]["error"] == "DOCUMENT_THROTTLED"
    assert datetime.fromisoformat(job["due_at"]) > datetime.now(timezone.utc) + timedelta(seconds=25)
    # Explicitly advance the synthetic due time, with a real ETag write.
    reset = clean(job)
    reset["due_at"] = datetime.now(timezone.utc).isoformat()
    runtime.store.batch("state", job["pk"], [Write("replace", job["id"], reset, job["_etag"])])
    monkeypatch.setattr(runtime, "publish", original)
    fresh = Runtime(LocalDocumentStore(runtime.store.path), runtime.cfg)
    assert run_job(fresh, message)
    assert fresh.store.get("state", row["pk"], row["id"])["state"] == "done"
    assert not run_job(fresh, message)


def test_document_entrypoints_load_without_importing_sql(tmp_path):
    env = {
        **os.environ,
        "ANKE_SPORTS_STORAGE_BACKEND": "documents-local",
        "ANKE_SPORTS_DOCUMENT_LOCAL_PATH": str(tmp_path / "isolated.db"),
    }
    code = """
import sys
from fastapi.testclient import TestClient
from app.main import app
import function_app
assert {'update_schedules', 'advance_calendar_window', 'dispatch_outbox', 'process_job'} <= {
    function.get_function_name() for function in function_app.app.get_functions()
}
with TestClient(app, headers={'Origin': 'http://127.0.0.1:3000'}) as client:
    assert client.get('/api/v1/health').json()['storage_backend'] == 'documents-local'
    assert client.post('/api/v1/auth/local').status_code == 200
assert 'app.db' not in sys.modules
assert 'app.sql_app' not in sys.modules
print('document runtime loaded; SQL modules absent')
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "SQL modules absent" in result.stdout
