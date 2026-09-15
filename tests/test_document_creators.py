from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.document_accounts import Outbox, now, owner_partition
from app.document_store import StoreError, Write, clean, partition_items
from tests.test_document_runtime import document_stack as document_stack, drain, follow, ics_rows

CHANNEL = "UC" + "a" * 22
VIDEO = "abcdefghijk"
HTTP_CLIENT = httpx.Client


@pytest.fixture
def creators(document_stack, monkeypatch):
    client, rt, rows, sources = document_stack
    rt.cfg.youtube_project_id = "synthetic-document-creators"
    rt.cfg.youtube_daily_budget = 10000
    monkeypatch.setenv("YOUTUBE_API_KEY", "synthetic-channel-key")
    rows = deepcopy(rows)
    rows[0]["title"] = "Lakers vs Warriors"
    rows[0]["participants"] = [
        {"id": "fixture:team", "name": "Lakers", "short_name": "LAL", "color": "#fff"},
        {"id": "fixture:GSW", "name": "Warriors", "short_name": "GSW", "color": "#fff"},
    ]
    rt.catalog.publish("fixture", rows, sources, expected_revision=1, complete=True)
    state = {
        "title": f"Lakers Warriors {rows[0]['local_date']} preview",
        "available": True,
        "name": "合成创作者",
        "calls": [],
        "upload_ids": [VIDEO],
        "videos_error": None,
    }

    def request(req):
        endpoint = req.url.path.rsplit("/", 1)[-1]
        state["calls"].append(endpoint)
        assert req.headers["x-goog-api-key"] == "synthetic-channel-key"
        if endpoint == "channels":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": CHANNEL,
                            "snippet": {"title": state["name"]},
                            "contentDetails": {"relatedPlaylists": {"uploads": "UUfixture"}},
                        }
                    ]
                },
            )
        if endpoint == "playlistItems":
            if state.get("uploads_handler"):
                return state["uploads_handler"](req)
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "contentDetails": {
                                "videoId": ident,
                                "videoPublishedAt": datetime.now(timezone.utc).isoformat(),
                            }
                        }
                        for ident in state["upload_ids"]
                    ]
                },
            )
        if endpoint == "commentThreads":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "snippet": {
                                "topLevelComment": {
                                    "snippet": {"textDisplay": "球迷讨论这场 Lakers Warriors 比赛"}
                                }
                            }
                        }
                    ]
                },
            )
        assert endpoint == "videos"
        if state["videos_error"]:
            return state["videos_error"](req)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": ident,
                        "snippet": {
                            "channelId": CHANNEL,
                            "title": state["title"],
                            "description": "",
                            "publishedAt": datetime.now(timezone.utc).isoformat(),
                        },
                        "status": {"privacyStatus": "public"},
                    }
                    for ident in req.url.params["id"].split(",")
                ]
                if state["available"]
                else []
            },
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(request), **kwargs)
    )
    client.post("/api/v1/auth/local").raise_for_status()
    follow(client)
    drain(rt)
    yield client, rt, rows, state


def add(client):
    revision = client.get("/api/v1/me/calendar").json()["revision"]
    data = {"url": CHANNEL, "expected_revision": revision}
    response = client.post("/api/v1/me/creators", json=data, headers={"Idempotency-Key": "creator-add-key"})
    assert response.status_code == 200, response.text
    return data, response


def refresh(client, rt):
    response = client.post(f"/api/v1/me/creators/{CHANNEL}/refresh")
    assert response.status_code == 200, response.text
    drain(rt)


def get_ics(client):
    address = client.get("/api/v1/me/feed/address").json()["url"]
    response = client.get(address)
    return response, ics_rows(response)


def test_creator_to_automatic_link_and_stable_ics_withdrawal(creators):
    client, rt, rows, state = creators
    data, response = add(client)
    replay = client.post("/api/v1/me/creators", json=data, headers={"Idempotency-Key": "creator-add-key"})
    assert replay.json() == response.json() and state["calls"] == ["channels"]
    drain(rt)
    profile = client.get("/api/v1/me/calendar").json()
    assert profile["creators"][0]["sync_status"] == "current", profile
    event = client.get("/api/v1/events/" + rows[0]["id"]).json()
    assert len(event["links"]) == 1 and event["links"][0]["origin"] == "automatic", event
    first, items = get_ics(client)
    assert len(items) == 3 and VIDEO in first.text
    state["title"] = "NBA trade news"
    refresh(client, rt)
    second, changed = get_ics(client)
    assert set(changed) == set(items) and VIDEO not in second.text
    assert client.get("/api/v1/events/" + rows[0]["id"]).json()["links"] == []
    refresh(client, rt)
    third, _ = get_ics(client)
    assert third.content == second.content and third.headers["etag"] == second.headers["etag"]
    assert rt.channels.schedule() == 0


def test_block_is_retained_after_rediscovery_and_pin_survives_creator_removal(creators):
    client, rt, rows, state = creators
    add(client)
    drain(rt)
    path = "/api/v1/events/" + rows[0]["id"]
    link = client.get(path).json()["links"][0]
    client.post("/api/v1/me/links/" + link["id"] + "/block").raise_for_status()
    drain(rt)
    refresh(client, rt)
    assert client.get(path).json()["links"] == []
    client.post("/api/v1/me/links/" + link["id"] + "/pin").raise_for_status()
    drain(rt)
    state["title"] = "NBA trade news"
    refresh(client, rt)
    assert client.get(path).json()["links"][0]["pinned"]
    impact = client.get(f"/api/v1/me/creators/{CHANNEL}/impact").json()
    assert impact["manual_retained"] == 1 and impact["automatic_removed"] == 0
    response = client.delete(
        f"/api/v1/me/creators/{CHANNEL}", params={"expected_revision": impact["revision"], "confirmed": True}
    )
    assert response.status_code == 200, response.text
    drain(rt)
    assert client.get(path).json()["links"][0]["pinned"]
    calls = len(state["calls"])
    assert rt.channels.schedule() == 0 and len(state["calls"]) == calls


def test_pause_keeps_existing_links_and_resume_matches_again(creators):
    client, rt, rows, state = creators
    add(client)
    drain(rt)
    user = client.get("/api/v1/me/calendar").json()
    data = {
        "expected_revision": user["revision"],
        "enabled": False,
        "preview": True,
        "recap": True,
        "scope_keys": [],
    }
    assert client.patch(f"/api/v1/me/creators/{CHANNEL}", json=data).status_code == 200
    drain(rt)
    assert len(client.get("/api/v1/events/" + rows[0]["id"]).json()["links"]) == 1
    assert client.post(f"/api/v1/me/creators/{CHANNEL}/refresh").status_code == 409
    row = rt.channels.get(CHANNEL)
    assert not rt.channels.interested(CHANNEL)
    assert row["payload"]["pending_job_id"] is None
    data.update(expected_revision=user["revision"] + 1, enabled=True)
    assert client.patch(f"/api/v1/me/creators/{CHANNEL}", json=data).status_code == 200
    drain(rt)
    assert rt.channels.interested(CHANNEL)


def test_ambiguous_review_confirm_ignore_and_missing_video(creators):
    client, rt, rows, state = creators
    state["title"] = "Lakers preview"
    add(client)
    drain(rt)
    pending = client.get("/api/v1/me/reviews").json()["items"]
    assert len(pending) == 1, pending
    item = pending[0]
    decision = {"decision": "confirm", "kind": "preview", "expected_updated_at": item["updated_at"]}
    response = client.post("/api/v1/me/reviews/" + item["id"], json=decision)
    assert response.status_code == 200 and response.json()["saved"], response.text
    assert client.post("/api/v1/me/reviews/" + item["id"], json=decision).status_code == 409
    drain(rt)
    link = client.get("/api/v1/events/" + rows[0]["id"]).json()["links"][0]
    assert link["origin"] == "confirmed" and link["pinned"]
    state["available"] = False
    refresh(client, rt)
    assert client.get("/api/v1/events/" + rows[0]["id"]).json()["links"] == []
    assert client.get("/api/v1/me/reviews").json()["items"] == []
    state["available"] = True
    refresh(client, rt)
    assert client.get("/api/v1/events/" + rows[0]["id"]).json()["links"][0]["pinned"]


def test_upload_absence_does_not_delete_known_video_and_failed_batch_preserves_links(creators):
    client, rt, rows, state = creators
    add(client)
    drain(rt)
    first, _ = get_ics(client)
    state["upload_ids"] = []
    refresh(client, rt)
    same, _ = get_ics(client)
    assert first.content == same.content
    old = rt.channels.video(CHANNEL, VIDEO)
    state["videos_error"] = lambda request: httpx.Response(200, json={"items": [{"id": VIDEO}]})
    refresh(client, rt)
    assert rt.channels.video(CHANNEL, VIDEO) == old
    assert client.get("/api/v1/events/" + rows[0]["id"]).json()["links"]
    assert rt.channels.get(CHANNEL)["payload"]["error"] == "CHANNEL_ID_MISMATCH"


def test_same_channel_is_shared_and_private_config_is_not(creators):
    from app.schemas import AddCreator, SaveFollows

    client, rt, rows, state = creators
    add(client)
    rt.accounts.ensure("second-owner")
    rt.save_follows(
        "second-owner",
        SaveFollows(expected_revision=0, follows=[{"type": "team", "source_key": "fixture:team"}]),
        None,
    )
    rt.creators.save("second-owner", AddCreator(url=CHANNEL, expected_revision=1))
    drain(rt)
    assert state["calls"].count("playlistItems") == 1
    first_links = rt.content.rows("local-reviewer")
    second_links = rt.content.rows("second-owner")
    assert len(first_links) == len(second_links) == 1
    assert first_links[0].id != second_links[0].id
    response = client.post("/api/v1/me/links/" + second_links[0].id + "/block")
    assert response.status_code == 404
    assert (
        len(list(partition_items(rt.store, "state", owner_partition("local-reviewer"), "video_match"))) == 1
    )


def pipeline(rt):
    rt.channels.register(CHANNEL, owner_partition("local-reviewer"))
    rt.channels.enqueue(CHANNEL)
    row = rt.channels.get(CHANNEL)
    claim = Outbox(rt.store).claim(row["pk"], row["payload"]["pending_job_id"])
    assert claim
    return claim


def clear_wait(rt):
    old = rt.channels.get(CHANNEL)
    job = rt.store.get("state", old["pk"], old["payload"]["pending_job_id"])
    channel, ready = clean(old), clean(job)
    channel["payload"]["retry_at"] = None
    ready["due_at"] = now()
    rt.store.batch(
        "state",
        old["pk"],
        [
            Write("replace", "channel", channel, old["_etag"]),
            Write("replace", job["id"], ready, job["_etag"]),
        ],
    )


def test_video_batch_and_stage_rollback_together_on_storage_conflict(creators, monkeypatch):
    client, rt, _, state = creators
    add(client)
    rt.channels.process(pipeline(rt))
    claim = pipeline(rt)
    assert claim["payload"]["stage"] == "videos"
    original, failed = rt.store.batch, []

    def conflict(container, pk, writes):
        if not failed and any(w.id.startswith("video:") for w in writes):
            failed.append(True)
            writes = [*writes[:-1], Write("replace", writes[-1].id, writes[-1].body, "stale-etag")]
        return original(container, pk, writes)

    monkeypatch.setattr(rt.store, "batch", conflict)
    rt.channels.process(claim)
    assert failed and rt.channels.video(CHANNEL, VIDEO) is None
    row = rt.channels.get(CHANNEL)
    job = rt.store.get("state", row["pk"], row["payload"]["pending_job_id"])
    assert job["state"] == "pending" and job["payload"]["stage"] == "videos"
    assert row["payload"]["last_success"] is None
    clear_wait(rt)
    rt.channels.process(pipeline(rt))
    assert rt.channels.video(CHANNEL, VIDEO)["title"] == state["title"]


def test_new_channel_lease_rejects_old_network_result(creators, monkeypatch):
    client, rt, _, _ = creators
    add(client)
    rt.channels.process(pipeline(rt))
    old_claim = pipeline(rt)
    request, newer = rt.youtube_request, []

    def delayed(endpoint, params):
        result = request(endpoint, params)
        job = Outbox(rt.store).current(old_claim)
        expired = clean(job)
        expired["due_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        rt.store.batch("state", job["pk"], [Write("replace", job["id"], expired, job["_etag"])])
        newer.append(Outbox(rt.store).claim(job["pk"], job["id"]))
        return result

    monkeypatch.setattr(rt, "youtube_request", delayed)
    with pytest.raises(StoreError, match="JOB_LEASE_LOST"):
        rt.channels.process(old_claim)
    assert rt.channels.video(CHANNEL, VIDEO) is None
    assert Outbox(rt.store).current(newer[0])["payload"]["lease"] == newer[0]["payload"]["lease"]
    monkeypatch.setattr(rt, "youtube_request", request)
    rt.channels.process(newer[0])
    assert rt.channels.video(CHANNEL, VIDEO)["available"]


def test_quota_wait_does_not_repeat_paid_upload_page_or_exhaust_attempts(creators):
    client, rt, _, state = creators
    rt.cfg.youtube_daily_budget = 2
    add(client)
    rt.channels.process(pipeline(rt))
    rt.channels.process(pipeline(rt))
    row = rt.channels.get(CHANNEL)
    assert row["payload"]["error"] == "YOUTUBE_BUDGET_EXHAUSTED"
    first = None
    for _ in range(7):
        job = rt.store.get("state", row["pk"], row["payload"]["pending_job_id"])
        ready = clean(job)
        ready["due_at"] = now()  # Simulate an early queue wake; authoritative cooldown remains future.
        rt.store.batch("state", job["pk"], [Write("replace", job["id"], ready, job["_etag"])])
        claim = pipeline(rt)
        first = first or claim
        rt.channels.process(claim)
    job = rt.store.get("state", row["pk"], row["payload"]["pending_job_id"])
    assert job["state"] == "pending" and job["payload"]["attempts"] == 0
    assert state["calls"] == ["channels", "playlistItems"]
    with pytest.raises(StoreError, match="JOB_LEASE_LOST"):
        Outbox(rt.store).current(first)


def test_failure_record_storage_error_propagates_without_acknowledging_job(creators, monkeypatch):
    client, rt, _, state = creators
    add(client)
    state["uploads_handler"] = lambda req: httpx.Response(503, text="private unavailable response")
    claim = pipeline(rt)
    original = rt.store.batch

    def fail(container, pk, writes):
        if any(w.id == "channel" and w.body["payload"].get("error") for w in writes):
            raise StoreError("SYNTHETIC_FAILURE_RECORD_DOWN", retryable=True)
        return original(container, pk, writes)

    monkeypatch.setattr(rt.store, "batch", fail)
    with pytest.raises(StoreError, match="SYNTHETIC_FAILURE_RECORD_DOWN"):
        rt.channels.process(claim)
    assert Outbox(rt.store).current(claim)["state"] == "running"
    assert rt.channels.get(CHANNEL)["payload"]["error"] == ""


def test_long_fifty_video_batch_uses_bounded_atomic_references(creators):
    client, rt, _, state = creators
    ids = [f"v{index:010d}" for index in range(50)]
    state["upload_ids"] = ids
    state["videos_error"] = lambda req: httpx.Response(
        200,
        json={
            "items": [
                {
                    "id": ident,
                    "snippet": {
                        "channelId": CHANNEL,
                        "title": state["title"],
                        "description": "长元数据🙂" * 2000,
                        "publishedAt": now(),
                    },
                    "status": {"privacyStatus": "public"},
                }
                for ident in ids
            ]
        },
    )
    add(client)
    rt.channels.process(pipeline(rt))
    rt.channels.process(pipeline(rt))
    channel = rt.channels.get(CHANNEL)
    rows = list(partition_items(rt.store, "state", channel["pk"], "video"))
    assert len(rows) == 50 and all(
        set(row["payload"]) == {"video_id", "value_ref", "updated_at"} for row in rows
    )
    assert len(rt.channels.video(CHANNEL, ids[0])["description"]) == 10000
    assert rt.channels.video(CHANNEL, ids[0])["comments"] == ["球迷讨论这场 Lakers Warriors 比赛"]


def test_reviews_ignore_scope_change_and_import_known_creator(creators):
    client, rt, _, state = creators
    state["title"] = "Lakers preview"
    add(client)
    drain(rt)
    item = client.get("/api/v1/me/reviews").json()["items"][0]
    user = client.get("/api/v1/me/calendar").json()
    changed = client.patch(
        f"/api/v1/me/creators/{CHANNEL}",
        json={
            "expected_revision": user["revision"],
            "enabled": True,
            "preview": False,
            "recap": True,
            "scope_keys": [],
        },
    )
    assert changed.status_code == 200
    assert client.get("/api/v1/me/reviews").json()["items"] == []
    assert (
        client.post(
            "/api/v1/me/reviews/" + item["id"],
            json={"decision": "confirm", "kind": "preview", "expected_updated_at": item["updated_at"]},
        ).status_code
        == 409
    )
    current = changed.json()
    data = {
        "config": current["config"],
        "mode": "replace",
        "dry_run": True,
        "expected_revision": current["revision"],
    }
    preview = client.post("/api/v1/me/config/import", json=data)
    assert preview.status_code == 200 and preview.json()["unresolved"] == [], preview.text
    bad = deepcopy(data)
    bad["config"]["creators"][0]["scope_keys"] = ["unknown:scope"]
    assert client.post("/api/v1/me/config/import", json=bad).json()["unresolved"] == ["unknown:scope"]
    drain(rt)
    current = client.get("/api/v1/me/calendar").json()
    data.update(dry_run=False, confirmation=preview.json()["confirmation"])
    assert client.post("/api/v1/me/config/import", json=data).status_code == 200


def test_many_ambiguous_associations_are_persisted_in_bounded_batches(creators):
    client, rt, rows, state = creators
    sources = [vars(source) for source in rt.catalog.capture().sources()]
    rows = [
        rows[0],
        *[
            {**deepcopy(rows[0]), "id": f"extra{index}", "source_key": f"fixture:extra:{index}"}
            for index in range(40)
        ],
    ]
    rt.catalog.publish("fixture", rows, sources, expected_revision=2, complete=True)
    add(client)
    drain(rt)
    pending = client.get("/api/v1/me/reviews").json()["items"]
    assert len(pending) == 41
    assert all("MULTIPLE_CANDIDATES" in item["reason_codes"] for item in pending)
    assert not rt.content.rows("local-reviewer")


def test_concurrent_block_fences_stale_personal_match_commit(creators, monkeypatch):
    from app.document_accounts import projection_job

    client, rt, rows, _ = creators
    add(client)
    drain(rt)
    link = rt.content.rows("local-reviewer")[0]
    account = rt.accounts.active("local-reviewer")
    job = projection_job(account["pk"], account["payload"]["revision"])
    job["payload"].update(operation="match_video", channel_id=CHANNEL, video_id=VIDEO)
    rt.store.batch("state", account["pk"], [Write("create", job["id"], job)])
    claim = Outbox(rt.store).claim(job["pk"], job["id"])
    original, blocked = rt.store.batch, []

    def interleaved(container, pk, writes):
        if not blocked and pk == account["pk"] and any(w.id == claim["id"] for w in writes):
            blocked.append(True)
            rt.content.override_link("local-reviewer", link.id, "block")
        return original(container, pk, writes)

    monkeypatch.setattr(rt.store, "batch", interleaved)
    with pytest.raises(StoreError, match="DOCUMENT_CONFLICT"):
        rt.matches.video(claim)
    assert blocked and client.get("/api/v1/events/" + rows[0]["id"]).json()["links"] == []
    rt.matches.video(claim)
    assert client.get("/api/v1/events/" + rows[0]["id"]).json()["links"] == []


def test_ignore_survives_new_title_and_user_cannot_review_another_account(creators):
    from app.schemas import ReviewDecision

    client, rt, rows, state = creators
    state["title"] = "Lakers preview"
    add(client)
    drain(rt)
    item = client.get("/api/v1/me/reviews").json()["items"][0]
    data = {"decision": "ignore", "kind": "preview", "expected_updated_at": item["updated_at"]}
    rt.accounts.ensure("unrelated-owner")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as rejected:
        rt.matches.decide("unrelated-owner", item["id"], ReviewDecision(**data))
    assert rejected.value.status_code == 404
    assert client.post("/api/v1/me/reviews/" + item["id"], json=data).status_code == 200
    state["title"] = f"Lakers Warriors {rows[0]['local_date']} preview"
    refresh(client, rt)
    assert client.get("/api/v1/me/reviews").json()["items"] == []
    assert not client.get("/api/v1/events/" + rows[0]["id"]).json()["links"]


def test_imported_match_identity_is_preserved_when_metadata_changes(creators):
    client, rt, _, state = creators
    state["title"] = "Lakers preview"
    add(client)
    drain(rt)
    pk = owner_partition("local-reviewer")
    old = list(partition_items(rt.store, "state", pk, "video_match"))[0]
    imported = clean(old)
    imported["id"], imported["payload"]["id"] = "match:" + "d" * 32, "d" * 32
    rt.store.batch(
        "state",
        pk,
        [Write("delete", old["id"], etag=old["_etag"]), Write("create", imported["id"], imported)],
    )
    state["title"] = "Lakers 赛前分析"
    refresh(client, rt)
    reviews = client.get("/api/v1/me/reviews").json()["items"]
    assert len(reviews) == 1 and reviews[0]["id"] == "d" * 32
    assert len(list(partition_items(rt.store, "state", pk, "video_match"))) == 1
