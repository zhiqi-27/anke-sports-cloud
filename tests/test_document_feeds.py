from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
from threading import Barrier
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi import HTTPException
from icalendar import Calendar
import pytest

from app.document_accounts import Accounts, Outbox, owner_partition, projection_job
from app.document_feeds import FeedPublisher
from app.document_store import Conflict, LocalDocumentStore, StoreError, Write, clean, partition_items


@pytest.fixture
def documents(tmp_path):
    store = LocalDocumentStore(tmp_path / "documents.db")
    cipher = Fernet(Fernet.generate_key())
    accounts, publisher, outbox = Accounts(store, cipher), FeedPublisher(store, cipher), Outbox(store)
    accounts.ensure("fixture-owner")
    return store, accounts, publisher, outbox


def event(index=0, days=1):
    start = (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0)
    return SimpleNamespace(
        id=f"e{index}",
        source_key=f"fixture:event:{index}",
        competition_id="fixture:league",
        sport="basketball",
        title=f"演示比赛 {index}",
        starts_at=start.isoformat(),
        local_date=start.date().isoformat(),
        time_precision="exact",
        timezone="UTC",
        duration=120,
        venue="演示场馆",
        status="scheduled",
        participants=[],
        provider="fixture",
        source_url="https://example.invalid/fixture",
        demo=True,
    )


def pending(store, outbox, user="fixture-owner"):
    for job in partition_items(store, "state", owner_partition(user), "outbox"):
        if claim := outbox.claim(job["pk"], job["id"]):
            return claim
    raise AssertionError("Expected a durable pending projection")


def follow(accounts, keys=("fixture:league",), *, key=None):
    account = accounts.active("fixture-owner")["payload"]
    config = {**account["config"], "follows": [{"type": "competition", "source_key": k} for k in keys]}
    return accounts.save_config("fixture-owner", config, account["revision"], key=key)


def drain(store, publisher, outbox, events):
    while True:
        jobs = list(partition_items(store, "state", owner_partition("fixture-owner"), "outbox"))
        claims = [claim for job in jobs if (claim := outbox.claim(job["pk"], job["id"]))]
        if not claims:
            return
        for claim in claims:
            publisher.publish("fixture-owner", claim, events, lambda _: [])


def test_configuration_receipt_and_job_are_atomic_and_retry_is_scoped(documents):
    store, accounts, _, _ = documents
    old = accounts.active("fixture-owner")["payload"]
    config = {**old["config"], "follows": [{"type": "competition", "source_key": "fixture:league"}]}
    result = accounts.save_config("fixture-owner", config, 0, key="same-command-key")
    before = list(partition_items(store, "state", owner_partition("fixture-owner"), "outbox"))
    assert accounts.save_config("fixture-owner", config, 0, key="same-command-key") == result
    assert list(partition_items(store, "state", owner_partition("fixture-owner"), "outbox")) == before
    with pytest.raises(HTTPException) as exc:
        accounts.save_config("fixture-owner", config, 0, key="same-command-key", operation="different")
    assert exc.value.detail["code"] == "IDEMPOTENCY_CONFLICT"
    accounts.ensure("other-owner")
    assert accounts.save_config("other-owner", config, 0, key="same-command-key")["revision"] == 1
    assert len(list(partition_items(store, "state", owner_partition("fixture-owner"), "receipt"))) == 1


def test_concurrent_configuration_commands_cannot_lose_changes_or_leave_receipts(documents, monkeypatch):
    store, accounts, _, _ = documents
    original, barrier = store.batch, Barrier(2)
    config = accounts.active("fixture-owner")["payload"]["config"]

    def batch(container, pk, writes):
        if any(w.id.startswith("receipt:") for w in writes):
            barrier.wait(timeout=3)
        return original(container, pk, writes)

    monkeypatch.setattr(store, "batch", batch)

    def command(index):
        try:
            accounts.save_config("fixture-owner", config, 0, key=f"command-{index}")
            return True
        except HTTPException as error:
            assert error.detail["code"] == "REVISION_CONFLICT"
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(command, [1, 2])) == [False, True]
    pk = owner_partition("fixture-owner")
    assert len(list(partition_items(store, "state", pk, "receipt"))) == 1
    assert len(list(partition_items(store, "state", pk, "outbox"))) == 2


def test_reschedule_rotation_repeat_reads_and_history_keep_published_identity(documents):
    store, accounts, publisher, outbox = documents
    events = [event(1), event(2, days=-2)]
    events[1].status = "cancelled"
    follow(accounts)
    drain(store, publisher, outbox, events)
    token = accounts.address("fixture-owner")
    first = publisher.read(token)
    rows = Calendar.from_ical(first.body).walk("VEVENT")
    uids = {str(row["SUMMARY"]): str(row["UID"]) for row in rows}
    assert len(rows) == 2 and all(int(row["SEQUENCE"]) == 1 for row in rows)
    pk = owner_partition("fixture-owner")
    stored = store.get("state", pk, "feed")
    assert publisher.read(token).body == first.body
    assert store.get("state", pk, "feed") == stored
    job = projection_job(pk, 1)
    store.batch("state", pk, [Write("create", job["id"], job)])
    claim = outbox.claim(pk, job["id"])
    assert publisher.publish("fixture-owner", claim, events, lambda _: []) is False
    assert publisher.read(token).updated_at == first.updated_at
    assert publisher.read(token).revision == first.revision
    assert publisher.read(token).etag == first.etag
    events[0].starts_at = (datetime.fromisoformat(events[0].starts_at) + timedelta(hours=2)).isoformat()
    follow(accounts)
    drain(store, publisher, outbox, events)
    changed = publisher.read(token)
    rows = Calendar.from_ical(changed.body).walk("VEVENT")
    assert {str(row["SUMMARY"]): str(row["UID"]) for row in rows} == uids
    assert sorted(int(row["SEQUENCE"]) for row in rows) == [1, 2]
    rotated = accounts.rotate("fixture-owner")
    assert rotated != token and publisher.read(rotated).body == changed.body
    with pytest.raises(HTTPException) as exc:
        publisher.read(token)
    assert exc.value.status_code == 404
    follow(accounts, ())
    drain(store, publisher, outbox, events)
    rows = Calendar.from_ical(publisher.read(rotated).body).walk("VEVENT")
    assert {str(row["UID"]) for row in rows} == {uids["演示比赛 2"]}
    assert [str(row["STATUS"]) for row in rows] == ["CANCELLED"]


def test_interrupted_large_generation_never_replaces_previous_calendar(documents, monkeypatch):
    store, accounts, publisher, outbox = documents
    follow(accounts)
    drain(store, publisher, outbox, [event()])
    token = accounts.address("fixture-owner")
    first = publisher.read(token)
    follow(accounts)
    claim = pending(store, outbox)
    original, chunks = store.batch, []

    def fail_manifest(container, pk, writes):
        if any(w.body and w.body["kind"] == "feed_manifest" for w in writes):
            raise StoreError("SYNTHETIC_STORAGE_OUTAGE", retryable=True)
        chunks.extend(w.id for w in writes if w.body and w.body["kind"] == "feed_chunk")
        return original(container, pk, writes)

    monkeypatch.setattr(store, "batch", fail_manifest)
    events = [event(i) for i in range(250)]
    for row in events:
        row.venue = "演示场馆 🏀 " * 200
    with pytest.raises(StoreError, match="SYNTHETIC_STORAGE_OUTAGE"):
        publisher.publish("fixture-owner", claim, events, lambda _: [])
    assert len(chunks) > 2
    assert publisher.read(token).body == first.body
    assert publisher.read(token).etag == first.etag
    assert outbox.current(claim)["state"] == "running"
    monkeypatch.setattr(store, "batch", original)
    assert publisher.publish("fixture-owner", claim, events, lambda _: []) is True
    assert len(Calendar.from_ical(publisher.read(token).body).walk("VEVENT")) == 250


@pytest.mark.parametrize("change", ["config", "delete", "lease"])
def test_late_changes_fence_a_publisher_even_after_all_chunks_are_written(documents, monkeypatch, change):
    store, accounts, publisher, outbox = documents
    follow(accounts)
    drain(store, publisher, outbox, [event()])
    pk = owner_partition("fixture-owner")
    first = store.get("state", pk, "feed")["payload"]
    follow(accounts)
    claim = pending(store, outbox)
    original = publisher.generations.prepare

    def raced(*args):
        generation = original(*args)
        if change == "config":
            follow(accounts, ())
        elif change == "delete":
            old = accounts.active("fixture-owner")
            row = clean(old)
            row["payload"]["deleted"] = True
            store.batch("state", pk, [Write("replace", "account", row, old["_etag"])])
        else:
            old = store.get("state", pk, claim["id"])
            row = clean(old)
            row["payload"]["lease"] = "a-new-worker-lease"
            store.batch("state", pk, [Write("replace", row["id"], row, old["_etag"])])
        return generation

    monkeypatch.setattr(publisher.generations, "prepare", raced)
    with pytest.raises((Conflict, StoreError)):
        publisher.publish("fixture-owner", claim, [event(44)], lambda _: [])
    assert store.get("state", pk, "feed")["payload"] == first
    assert store.get("state", pk, claim["id"])["state"] != "done"
    if change == "delete":
        with pytest.raises(HTTPException):
            accounts.ensure("fixture-owner")


def test_independent_process_reopens_persisted_documents_without_sql_runtime(documents):
    store, accounts, publisher, outbox = documents
    follow(accounts)
    drain(store, publisher, outbox, [event()])
    command = """import json,sys
from app.document_store import LocalDocumentStore
from app.document_accounts import owner_partition
from app.document_feeds import FeedGenerations
store=LocalDocumentStore(sys.argv[1]);pk=owner_partition('fixture-owner')
feed=store.get('state',pk,'feed')
snapshot=FeedGenerations(store).load(pk,feed['payload']['generation'])
assert 'app.db' not in sys.modules
print(json.dumps({'events':len(snapshot['projections']),'body_present':bool(snapshot['body'])}))
"""
    env = {**os.environ, "ANKE_SPORTS_STORAGE_BACKEND": "documents-local"}
    result = subprocess.run(
        [sys.executable, "-c", command, str(store.path)], capture_output=True, text=True, env=env, timeout=20
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"events": 1, "body_present": True}


def test_expired_lease_is_reclaimed_and_original_worker_cannot_complete(documents):
    store, _, _, outbox = documents
    pk = owner_partition("fixture-owner")
    job = next(partition_items(store, "state", pk, "outbox"))
    old = clean(job)
    old["due_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    store.batch("state", pk, [Write("replace", job["id"], old, job["_etag"])])
    claim = outbox.claim(pk, job["id"], instant=datetime.now(timezone.utc) - timedelta(minutes=6))
    with pytest.raises(StoreError, match="LEASE_LOST"):
        outbox.completion(claim)
    replacement = outbox.claim(pk, job["id"])
    assert replacement["payload"]["attempts"] == 2
    assert replacement["payload"]["lease"] != claim["payload"]["lease"]
    with pytest.raises(StoreError, match="LEASE_LOST"):
        outbox.completion(claim)
    store.batch("state", pk, [outbox.completion(replacement)])
    assert outbox.claim(pk, job["id"]) is None


def test_tombstone_precedes_idempotent_receipt_replay(documents):
    store, accounts, _, _ = documents
    config = accounts.active("fixture-owner")["payload"]["config"]
    accounts.save_config("fixture-owner", config, 0, key="fixture-saved-key")
    row = accounts.active("fixture-owner")
    changed = clean(row)
    changed["payload"]["deleted"] = True
    store.batch("state", row["pk"], [Write("replace", "account", changed, row["_etag"])])
    with pytest.raises(HTTPException) as exc:
        accounts.save_config("fixture-owner", config, 0, key="fixture-saved-key")
    assert exc.value.detail["code"] == "ACCOUNT_DELETED"


def test_corrupt_published_chunk_is_never_served_as_a_partial_calendar(documents):
    store, accounts, publisher, outbox = documents
    follow(accounts)
    drain(store, publisher, outbox, [event()])
    token = accounts.address("fixture-owner")
    pk = owner_partition("fixture-owner")
    feed = store.get("state", pk, "feed")
    manifest = store.get("state", pk, "generation:" + feed["payload"]["generation"])
    part = store.get("state", pk, manifest["payload"]["pieces"][0]["id"])
    store.batch("state", pk, [Write("delete", part["id"], etag=part["_etag"])])
    with pytest.raises(StoreError, match="GENERATION_INCOMPLETE"):
        publisher.read(token)
