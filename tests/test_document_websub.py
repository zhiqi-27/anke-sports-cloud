from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.document_accounts import Outbox, now
from app.document_runtime import Runtime
from app.document_store import Conflict, LocalDocumentStore, StoreError, Write, clean, partition_items
from app.document_worker import run_job
from app.websub_rules import HUB, topic
from tests.test_document_creators import (
    CHANNEL,
    VIDEO,
    HTTP_CLIENT,
    add,
    creators as creators,
    get_ics,
)
from tests.test_document_runtime import document_stack as document_stack, drain


@pytest.fixture
def hooks(creators, monkeypatch):
    client, rt, events, upstream = creators
    rt.cfg.youtube_websub_enabled = True
    rt.cfg.public_url = "https://synthetic-hook.example"
    original = httpx.Client
    hub = {"calls": [], "verify": True, "response": 202, "error": None, "before_response": None}

    def send(request):
        if str(request.url) != HUB:
            with original() as api:
                return api.send(request)
        params = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        hub["calls"].append(params)
        # A second storage connection sees committed intent before the Hub callback.
        with_store = LocalDocumentStore(rt.store.path)
        other = Runtime(with_store, rt.cfg)
        row = other.websub.get(CHANNEL)
        assert row["payload"]["state"] in {"pending", "renewing", "unsubscribing"}
        assert row["payload"]["pending_until"] > now()
        assert params["hub.topic"] == topic(CHANNEL)
        assert params["hub.secret"] not in row["payload"]["secret_ciphertext"]
        if hub["verify"]:
            query = {
                "hub.mode": params["hub.mode"],
                "hub.topic": params["hub.topic"],
                "hub.challenge": "synthetic-challenge-" + str(len(hub["calls"])),
                "hub.lease_seconds": "1000",
            }
            response = client.get(urlsplit(params["hub.callback"]).path, params=query)
            assert response.status_code == 200 and response.text == query["hub.challenge"]
        if hub["before_response"]:
            hub["before_response"]()
        if hub["error"]:
            raise hub["error"](request)
        return httpx.Response(hub["response"], headers={"Retry-After": "900"})

    monkeypatch.setattr(httpx, "Client", lambda **kw: HTTP_CLIENT(transport=httpx.MockTransport(send), **kw))
    yield client, rt, events, upstream, hub


def start(hooks):
    client, rt, _, _, hub = hooks
    add(client)
    drain(rt)
    assert len(hub["calls"]) == 1
    return rt.websub.get(CHANNEL)


def edit(rt, mutate):
    old = rt.websub.get(CHANNEL)
    changed = clean(old)
    mutate(changed["payload"])
    rt.store.batch("state", old["pk"], [Write("replace", "websub", changed, old["_etag"])])
    return rt.websub.get(CHANNEL)


def due(rt, job_id):
    pk = rt.channels.get(CHANNEL)["pk"]
    job = rt.store.get("state", pk, job_id)
    changed = clean(job)
    changed["due_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    rt.store.batch("state", pk, [Write("replace", job_id, changed, job["_etag"])])
    return {"version": 1, "pk": pk, "job_id": job_id}


def body(ids=(VIDEO,), version="2026-09-10T00:00:00Z", title="Ignored notification title", spacing=""):
    entries = "".join(
        f"<entry><yt:videoId>{ident}</yt:videoId>{spacing}"
        f"<yt:channelId>{CHANNEL}</yt:channelId><updated>{version}</updated><title>{title}</title></entry>"
        for ident in ids
    )
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:yt="http://www.youtube.com/xml/schemas/2015">' + entries + "</feed>"
    ).encode()


def notify(client, rt, payload, *, signature=None):
    row = rt.websub.get(CHANNEL)["payload"]
    secret = rt.cfg.cipher().decrypt(row["secret_ciphertext"].encode())
    signature = signature or "sha1=" + hmac.new(secret, payload, hashlib.sha1).hexdigest()
    return client.post(
        "/webhooks/youtube/" + row["callback_id"], content=payload, headers={"X-Hub-Signature": signature}
    )


def notices(rt, kind):
    return list(partition_items(rt.store, "state", rt.channels.get(CHANNEL)["pk"], "youtube_notice_" + kind))


def test_notification_metadata_refresh_changes_same_ics_and_semantic_duplicate_dedup(hooks):
    client, rt, events, upstream, hub = hooks
    sync = start(hooks)
    assert sync["payload"]["state"] == "verified"
    lease = sync["payload"]["lease_expires_at"]
    q = {
        "hub.mode": "subscribe",
        "hub.topic": topic(CHANNEL),
        "hub.challenge": "synthetic-challenge-1",
        "hub.lease_seconds": "1000",
    }
    path = "/webhooks/youtube/" + sync["payload"]["callback_id"]
    assert client.get(path, params=q).text == q["hub.challenge"]
    assert rt.websub.get(CHANNEL)["payload"]["lease_expires_at"] == lease
    before, rows = get_ics(client)
    upstream["title"] = "Unrelated trade news"
    calls = len(upstream["calls"])
    assert notify(client, rt, body()).status_code == 204
    assert notify(client, rt, body(spacing="\n  ", title="Different untrusted title")).status_code == 204
    assert len(notices(rt, "pending")) == 1 and len(upstream["calls"]) == calls
    drain(rt)
    after, updated = get_ics(client)
    assert set(rows) == set(updated)
    affected = next(uid for uid in rows if VIDEO in str(rows[uid]["DESCRIPTION"]))
    assert int(updated[affected]["SEQUENCE"]) == int(rows[affected]["SEQUENCE"]) + 1
    assert VIDEO not in after.text and before.content != after.content
    assert upstream["calls"][calls:] == ["videos"]
    assert len(notices(rt, "done")) == 1 and not notices(rt, "pending")
    assert notify(client, rt, body()).status_code == 204
    drain(rt)
    assert get_ics(client)[0].content == after.content


def test_bad_signature_foreign_channel_entities_size_and_disabled_are_not_accepted(hooks):
    client, rt, *_ = hooks
    start(hooks)
    bad_bodies = [
        body().replace(CHANNEL.encode(), ("UC" + "z" * 22).encode()),
        b'<!DOCTYPE feed [<!ENTITY bad SYSTEM "file:///unreadable">]><feed>&bad;</feed>',
        body(version="invalid"),
        body(ids=["bad"]),
        body(ids=[VIDEO] * 51),
    ]
    assert notify(client, rt, body(), signature="sha1=" + "0" * 40).status_code == 204
    for payload in bad_bodies:
        assert notify(client, rt, payload).status_code == 204
    assert notify(client, rt, b"x" * 65537).status_code == 413
    assert not notices(rt, "pending")
    rt.cfg.youtube_websub_enabled = False
    assert notify(client, rt, body()).status_code == 204 and not notices(rt, "pending")


def test_receipt_and_wakeup_rollback_together_and_retry_is_safe(hooks, monkeypatch):
    client, rt, *_ = hooks
    start(hooks)
    old = rt.websub.get(CHANNEL)
    api_store = client.app.state.runtime.store
    original = api_store.batch

    def fail(bucket, pk, writes):
        if any(w.body and w.body.get("kind") == "youtube_notice_pending" for w in writes):
            raise StoreError("SYNTHETIC_STORAGE_OUTAGE", retryable=True)
        return original(bucket, pk, writes)

    monkeypatch.setattr(api_store, "batch", fail)
    assert notify(client, rt, body()).status_code == 503
    assert not notices(rt, "pending") and rt.websub.get(CHANNEL)["_etag"] == old["_etag"]
    monkeypatch.setattr(api_store, "batch", original)
    assert notify(client, rt, body()).status_code == 204
    drain(rt)
    assert len(notices(rt, "done")) == 1


def test_fifty_notifications_are_bounded_and_shared_pipeline_drains_all(hooks):
    client, rt, _, upstream, _ = hooks
    start(hooks)
    ids = [f"v{number:010d}" for number in range(50)]
    assert notify(client, rt, body(ids)).status_code == 204
    assert len(notices(rt, "pending")) == 50
    calls = upstream["calls"].count("videos")
    drain(rt)
    assert not notices(rt, "pending") and len(notices(rt, "done")) == 50
    assert upstream["calls"].count("videos") == calls + 2


def test_renewal_keeps_callback_and_secret_and_old_lease_accepts_updates(hooks):
    client, rt, _, _, hub = hooks
    row = start(hooks)
    original = hub["calls"][0]
    edit(rt, lambda v: v.update(renew_at="2000-01-01T00:00:00+00:00"))
    hub["verify"] = False
    rt.channels.schedule()
    drain(rt)
    renewing = rt.websub.get(CHANNEL)["payload"]
    assert renewing["state"] == "renewing" and len(hub["calls"]) == 2
    assert hub["calls"][1]["hub.callback"] == original["hub.callback"]
    assert hub["calls"][1]["hub.secret"] == original["hub.secret"]
    assert renewing["lease_expires_at"] == row["payload"]["lease_expires_at"]
    assert notify(client, rt, body()).status_code == 204 and len(notices(rt, "pending")) == 1
    q = {
        "hub.mode": "subscribe",
        "hub.topic": topic(CHANNEL),
        "hub.challenge": "new-lease",
        "hub.lease_seconds": "2000",
    }
    assert client.get(urlsplit(original["hub.callback"]).path, params=q).text == "new-lease"
    assert rt.websub.get(CHANNEL)["payload"]["lease_expires_at"] > renewing["lease_expires_at"]
    drain(rt)


def test_uncertain_request_does_not_resend_early_and_callback_can_still_verify(hooks):
    client, rt, _, _, hub = hooks
    hub["verify"] = False
    hub["error"] = lambda req: httpx.ReadTimeout("synthetic", request=req)
    row = start(hooks)
    job_id = row["payload"]["pending_job_id"]
    for _ in range(7):
        run_job(rt, due(rt, job_id))
    assert len(hub["calls"]) == 1
    job = rt.store.get("state", row["pk"], job_id)
    assert job["payload"]["attempts"] == 1 and job["state"] == "pending"
    q = {
        "hub.mode": "subscribe",
        "hub.topic": topic(CHANNEL),
        "hub.challenge": "late",
        "hub.lease_seconds": "1000",
    }
    path = "/webhooks/youtube/" + row["payload"]["callback_id"]
    assert client.get(path, params=q).text == "late"
    run_job(rt, due(rt, job_id))
    assert rt.websub.get(CHANNEL)["payload"]["state"] == "verified" and len(hub["calls"]) == 1


def test_verification_before_outbound_error_is_not_overwritten(hooks):
    _, rt, _, _, hub = hooks
    hub["error"] = lambda req: httpx.ReadTimeout("synthetic", request=req)
    row = start(hooks)
    assert row["payload"]["state"] == "verified" and not row["payload"]["error"]
    assert row["payload"]["pending_job_id"] is None


def test_last_follower_pause_unsubscribes_and_resume_reestablishes(hooks):
    client, rt, _, _, hub = hooks
    start(hooks)
    user = client.get("/api/v1/me/calendar").json()
    value = user["config"]["creators"][0]
    value = {k: v for k, v in value.items() if k != "channel_id"}
    assert (
        client.patch(
            f"/api/v1/me/creators/{CHANNEL}",
            json={**value, "enabled": False, "expected_revision": user["revision"]},
        ).status_code
        == 200
    )
    assert notify(client, rt, body()).status_code == 204 and not notices(rt, "pending")
    drain(rt)
    assert hub["calls"][-1]["hub.mode"] == "unsubscribe"
    assert rt.websub.get(CHANNEL)["payload"]["state"] == "unsubscribed"
    user = client.get("/api/v1/me/calendar").json()
    assert (
        client.patch(
            f"/api/v1/me/creators/{CHANNEL}",
            json={**value, "enabled": True, "expected_revision": user["revision"]},
        ).status_code
        == 200
    )
    drain(rt)
    assert hub["calls"][-1]["hub.mode"] == "subscribe" and rt.websub.status(CHANNEL) == "verified"


def test_expired_pending_denied_wrong_topic_and_invalid_lease(hooks):
    client, rt, _, _, hub = hooks
    hub["verify"] = False
    row = start(hooks)
    path = "/webhooks/youtube/" + row["payload"]["callback_id"]
    query = {
        "hub.mode": "subscribe",
        "hub.topic": topic(CHANNEL),
        "hub.challenge": "test",
        "hub.lease_seconds": "1000",
    }
    assert client.get(path, params={**query, "hub.topic": topic("other")}).status_code == 404
    assert client.get(path, params={**query, "hub.mode": "unsubscribe"}).status_code == 404
    assert client.get(path, params={**query, "hub.lease_seconds": "0"}).status_code == 400
    edit(rt, lambda v: v.update(pending_until="2000-01-01T00:00:00+00:00"))
    assert client.get(path, params=query).status_code == 404
    edit(
        rt, lambda v: v.update(pending_until=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
    )
    assert (
        client.get(
            path,
            params={
                "hub.mode": "denied",
                "hub.topic": topic(CHANNEL),
                "hub.reason": "untrusted upstream body",
            },
        ).status_code
        == 200
    )
    row = rt.websub.get(CHANNEL)
    assert row["payload"]["state"] == "denied" and row["payload"]["error"] == "HUB_DENIED"
    assert "untrusted" not in str(row)


def test_lost_worker_lease_after_http_cannot_finish_new_claim(hooks):
    client, rt, _, _, hub = hooks
    hub["verify"] = False
    row = start(hooks)
    ident = row["payload"]["pending_job_id"]
    edit(rt, lambda v: v.update(pending_until=None, retry_at=None))
    message = due(rt, ident)
    replacement = []

    def take_over():
        due(rt, ident)
        replacement.append(Outbox(rt.store).claim(row["pk"], ident))

    hub["before_response"] = take_over
    with pytest.raises(StoreError, match="JOB_LEASE_LOST"):
        run_job(rt, message)
    current = rt.store.get("state", row["pk"], ident)
    assert current["state"] == "running" and current["payload"]["lease"] == replacement[0]["payload"]["lease"]


def test_stale_receipts_pruned_but_pending_work_retained(hooks):
    client, rt, *_ = hooks
    start(hooks)
    notify(client, rt, body())
    drain(rt)
    notify(client, rt, body(version="2026-09-10T00:00:01Z"))
    for row in notices(rt, "done") + notices(rt, "pending"):
        value = clean(row)
        value["payload"]["received_at"] = "2000-01-01T00:00:00+00:00"
        rt.store.batch("state", row["pk"], [Write("replace", row["id"], value, row["_etag"])])
    rt.websub.prune(CHANNEL)
    assert not notices(rt, "done") and len(notices(rt, "pending")) == 1


def test_unverified_hub_requests_eventually_cool_down(hooks):
    _, rt, _, _, hub = hooks
    hub["verify"] = False
    row = start(hooks)
    ident = row["payload"]["pending_job_id"]
    for _ in range(4):
        edit(rt, lambda v: v.update(pending_until="2000-01-01T00:00:00+00:00", retry_at=None))
        run_job(rt, due(rt, ident))
    assert len(hub["calls"]) == 5
    assert rt.store.get("state", row["pk"], ident)["state"] == "failed"
    assert rt.websub.get(CHANNEL)["payload"]["retry_at"] > now()
    assert not rt.websub.schedule_channel(CHANNEL)


def test_notification_during_running_poll_is_drained_after_older_response(hooks, monkeypatch):
    client, rt, _, upstream, _ = hooks
    start(hooks)
    original = rt.youtube_request
    injected = []

    def changed_during_request(endpoint, params):
        result = original(endpoint, params)
        if endpoint == "videos" and not injected:
            injected.append(True)
            upstream["title"] = "Unrelated trade news after old fetch"
            assert notify(client, rt, body()).status_code == 204
        return result

    monkeypatch.setattr(rt, "youtube_request", changed_during_request)
    assert client.post(f"/api/v1/me/creators/{CHANNEL}/refresh").status_code == 200
    drain(rt)
    assert injected and not notices(rt, "pending")
    assert VIDEO not in get_ics(client)[0].text
    assert rt.channels.video(CHANNEL, VIDEO)["title"] == upstream["title"]


def test_notice_processing_commit_failure_preserves_receipt_and_old_video(hooks, monkeypatch):
    client, rt, _, upstream, _ = hooks
    start(hooks)
    previous = rt.channels.video(CHANNEL, VIDEO)
    upstream["title"] = "Changed synthetic metadata"
    notify(client, rt, body())
    original = rt.store.batch

    def conflict(bucket, pk, writes):
        if any(w.body and w.body.get("kind") == "youtube_notice_done" for w in writes):
            raise Conflict()
        return original(bucket, pk, writes)

    monkeypatch.setattr(rt.store, "batch", conflict)
    drain(rt)
    assert rt.channels.video(CHANNEL, VIDEO) == previous
    assert len(notices(rt, "pending")) == 1 and not notices(rt, "done")
    monkeypatch.setattr(rt.store, "batch", original)
    channel = rt.channels.get(CHANNEL)
    changed = clean(channel)
    changed["payload"]["retry_at"] = None
    rt.store.batch("state", channel["pk"], [Write("replace", "channel", changed, channel["_etag"])])
    run_job(rt, due(rt, channel["payload"]["pending_job_id"]))
    drain(rt)
    assert not notices(rt, "pending") and len(notices(rt, "done")) == 1


def test_terminal_channel_failure_cooldown_applies_to_notification_wakeup(hooks):
    client, rt, _, upstream, _ = hooks
    start(hooks)
    notify(client, rt, body())
    upstream["videos_error"] = lambda req: httpx.Response(503, json={"error": {}})
    drain(rt)
    channel = rt.channels.get(CHANNEL)
    ident = channel["payload"]["pending_job_id"]
    for _ in range(4):
        channel = rt.channels.get(CHANNEL)
        changed = clean(channel)
        changed["payload"]["retry_at"] = None
        rt.store.batch("state", channel["pk"], [Write("replace", "channel", changed, channel["_etag"])])
        run_job(rt, due(rt, ident))
    channel = rt.channels.get(CHANNEL)
    assert rt.store.get("state", channel["pk"], ident)["state"] == "failed"
    assert channel["payload"]["retry_at"] == channel["payload"]["next_poll_at"]
    calls = len(upstream["calls"])
    rt.channels.schedule()
    drain(rt)
    assert len(upstream["calls"]) == calls and len(notices(rt, "pending")) == 1


def test_denied_active_lease_is_retired_and_polling_still_works(hooks):
    client, rt, _, _, _ = hooks
    row = start(hooks)
    path = "/webhooks/youtube/" + row["payload"]["callback_id"]
    assert client.get(path, params={"hub.mode": "denied", "hub.topic": topic(CHANNEL)}).status_code == 200
    retired = rt.websub.get(CHANNEL)["payload"]
    assert retired["state"] == "denied" and retired["lease_expires_at"] is None
    assert not rt.websub.schedule_channel(CHANNEL)
    assert notify(client, rt, body()).status_code == 204 and not notices(rt, "pending")
    assert client.post(f"/api/v1/me/creators/{CHANNEL}/refresh").status_code == 200
    drain(rt)
    assert VIDEO in get_ics(client)[0].text


def test_failed_hub_result_storage_failure_never_acknowledges_job(hooks, monkeypatch):
    client, rt, _, _, hub = hooks
    hub["verify"] = False
    hub["error"] = lambda req: httpx.ReadTimeout("synthetic", request=req)
    original = rt.store.batch

    def outage(bucket, pk, writes):
        if any(
            w.body
            and w.body.get("kind") == "websub"
            and w.body["payload"]["error"] == "UPSTREAM_NETWORK_ERROR"
            for w in writes
        ):
            raise StoreError("SYNTHETIC_STORAGE_OUTAGE", retryable=True)
        return original(bucket, pk, writes)

    monkeypatch.setattr(rt.store, "batch", outage)
    add(client)
    with pytest.raises(StoreError, match="SYNTHETIC_STORAGE_OUTAGE"):
        drain(rt)
    row = rt.websub.get(CHANNEL)
    job = rt.store.get("state", row["pk"], row["payload"]["pending_job_id"])
    assert job["state"] == "running" and row["payload"]["state"] == "pending"


def test_sha256_notification_and_nanosecond_upstream_versions_are_distinct(hooks):
    client, rt, *_ = hooks
    row = start(hooks)
    secret = rt.cfg.cipher().decrypt(row["payload"]["secret_ciphertext"].encode())
    for version in ["2026-09-10T00:00:00.000000001Z", "2026-09-10T00:00:00.000000002Z"]:
        payload = body(version=version)
        signature = "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()
        assert notify(client, rt, payload, signature=signature).status_code == 204
    assert len(notices(rt, "pending")) == 2
