from copy import deepcopy
import json

from fastapi import HTTPException
import pytest

from app.document_accounts import Outbox, owner_partition
from app.document_runtime import Runtime
from app.document_store import LocalDocumentStore, StoreError, Write, clean, partition_items
from app.schemas import AddLink, SaveFollows
from app.security import digest
from tests import test_document_runtime
from tests.test_document_runtime import drain, follow, ics_rows

document_stack = test_document_runtime.document_stack


OWNER = "local-reviewer"
VIDEO = "https://www.youtube.com/watch?v=AnkeDemo001"
LINK = {"url": "https://youtu.be/AnkeDemo001?utm_source=fixture", "title": "演示前瞻", "kind": "preview"}


def state(runtime, kind):
    return list(partition_items(runtime.store, "state", owner_partition(OWNER), kind))


def login(client, runtime, *, followed=True):
    client.post("/api/v1/auth/local").raise_for_status()
    if followed:
        follow(client)
    drain(runtime)
    return client.get("/api/v1/me/feed/address").json()["url"]


def test_manual_link_block_reattach_pin_and_exact_replay_preserve_uid(document_stack):
    client, runtime, _, _ = document_stack
    address = login(client, runtime)
    original = ics_rows(client.get(address))
    command = {"Idempotency-Key": "manual-link-command"}
    result = client.post("/api/v1/events/e0/links", json=LINK, headers=command)
    assert result.status_code == 200, result.text
    value = result.json()
    link = value["event"]["links"][0]
    assert link["url"] == VIDEO and link["pinned"] and link["origin"] == "manual"
    assert link["broadcast"] is None  # A submitted URL is not verified official content.
    drain(runtime)
    attached = ics_rows(client.get(address))
    assert attached.keys() == original.keys()
    changed = [uid for uid in attached if VIDEO in str(attached[uid]["DESCRIPTION"])]
    assert len(changed) == 1
    uid = changed[0]
    assert int(attached[uid]["SEQUENCE"]) == int(original[uid]["SEQUENCE"]) + 1
    jobs = state(runtime, "outbox")
    assert client.post("/api/v1/events/e0/links", json=LINK, headers=command).json() == value
    assert state(runtime, "outbox") == jobs
    assert (
        client.post(
            "/api/v1/events/e0/links", json={**LINK, "title": "different"}, headers=command
        ).status_code
        == 409
    )
    blocked = client.post(
        f"/api/v1/me/links/{value['id']}/block", headers={"Idempotency-Key": "block-link-command"}
    )
    assert blocked.json() == {"blocked": True}
    drain(runtime)
    removed = ics_rows(client.get(address))
    assert VIDEO not in str(removed[uid]["DESCRIPTION"])
    assert int(removed[uid]["SEQUENCE"]) == int(attached[uid]["SEQUENCE"]) + 1
    reattached = client.post("/api/v1/events/e0/links", json={**LINK, "title": "新标题仍屏蔽"})
    assert reattached.json()["id"] == value["id"] and reattached.json()["event"]["links"] == []
    drain(runtime)
    unchanged = client.get(address)
    assert ics_rows(unchanged)[uid] == removed[uid]
    assert client.post(f"/api/v1/me/links/{value['id']}/pin").json() == {"pinned": True}
    drain(runtime)
    assert VIDEO in str(ics_rows(client.get(address))[uid]["DESCRIPTION"])
    assert len(state(runtime, "link")) == 1
    runtime.accounts.ensure("other-owner")
    assert (
        runtime.event_view(runtime.content.event("e0"), runtime.accounts.active("other-owner")["payload"])[
            "links"
        ]
        == []
    )
    with pytest.raises(HTTPException) as error:
        runtime.content.override_link("other-owner", value["id"], "block")
    assert error.value.status_code == 404
    client.post("/api/v1/auth/logout").raise_for_status()
    assert client.get("/api/v1/events/e0").json()["links"] == []
    assert client.post("/api/v1/events/e0/links", json=LINK).status_code == 401


def test_failed_link_transaction_and_invalid_url_leave_no_partial_command(document_stack, monkeypatch):
    client, runtime, _, _ = document_stack
    login(client, runtime)
    account = runtime.accounts.active(OWNER)
    jobs = state(runtime, "outbox")
    batch = runtime.store.batch

    def fail(container, pk, writes):
        if any(write.id.startswith("link:") for write in writes):
            # A real failed ETag inside the adapter transaction must roll back its earlier writes.
            writes = [
                Write("replace", write.id, write.body, '"invalid"') if write.id.startswith("link:") else write
                for write in writes
            ]
        return batch(container, pk, writes)

    monkeypatch.setattr(runtime.store, "batch", fail)
    result = client.post(
        "/api/v1/events/e0/links", json=LINK, headers={"Idempotency-Key": "failed-link-command"}
    )
    assert result.status_code == 409, result.text
    assert runtime.accounts.active(OWNER) == account
    assert state(runtime, "link") == []
    assert runtime.store.get("state", account["pk"], "receipt:" + digest("failed-link-command")) is None
    assert state(runtime, "outbox") == jobs
    monkeypatch.setattr(runtime.store, "batch", batch)
    for url in ["https://www.youtube.com/", "https://127.0.0.1/private", VIDEO + "&token=fixture"]:
        assert client.post("/api/v1/events/e0/links", json={**LINK, "url": url}).status_code == 400
    assert client.post("/api/v1/events/missing/links", json=LINK).status_code == 404
    assert runtime.accounts.active(OWNER) == account and state(runtime, "outbox") == jobs


def test_explicit_selection_reset_cancellation_and_stale_revision(document_stack):
    client, runtime, _, _ = document_stack
    address = login(client, runtime, followed=False)
    assert ics_rows(client.get(address)) == {}
    result = client.put("/api/v1/events/e0/selection", json={"expected_revision": 0, "state": "include"})
    assert result.status_code == 200 and result.json()["included"]
    drain(runtime)
    original = ics_rows(client.get(address))
    assert len(original) == 1
    assert (
        client.put(
            "/api/v1/events/e0/selection", json={"expected_revision": 0, "state": "exclude"}
        ).status_code
        == 409
    )
    assert (
        client.put("/api/v1/events/e0/selection", json={"expected_revision": 1, "state": "reset"}).json()[
            "included"
        ]
        is False
    )
    drain(runtime)
    hidden = ics_rows(client.get(address))
    assert hidden == {}
    assert runtime.accounts.active(OWNER)["payload"]["config"]["event_overrides"] == []


def test_import_preview_confirmation_owner_binding_merge_replace_and_replay(document_stack):
    client, runtime, _, _ = document_stack
    login(client, runtime)
    request = {
        "expected_revision": 1,
        "config": {
            "preferences": {"locale": "en"},
            "link_overrides": [{"event_key": "fixture:event:0", "url": LINK["url"], "state": "block"}],
        },
    }
    account, jobs = runtime.accounts.active(OWNER), state(runtime, "outbox")
    preview = client.post("/api/v1/me/config/import", json=request)
    assert preview.status_code == 200 and preview.json()["applied"] is False
    assert runtime.accounts.active(OWNER) == account and state(runtime, "outbox") == jobs
    data = {**request, "dry_run": False, "confirmation": preview.json()["confirmation"]}
    assert client.post("/api/v1/me/config/import", json={**data, "confirmation": "wrong"}).status_code == 400
    runtime.accounts.ensure("other-owner")
    runtime.accounts.save_config("other-owner", account["payload"]["config"], 0)
    from app.schemas import ImportInput

    with pytest.raises(HTTPException) as error:
        runtime.content.import_config("other-owner", ImportInput.model_validate(data), None)
    assert error.value.detail["code"] == "PREVIEW_REQUIRED"
    headers = {"Idempotency-Key": "import-content-command"}
    saved = client.post("/api/v1/me/config/import", json=data, headers=headers)
    assert saved.status_code == 200 and saved.json()["applied"]
    drain(runtime)
    assert client.post("/api/v1/me/config/import", json=data, headers=headers).json() == saved.json()
    config = client.get("/api/v1/me/config/export").json()
    assert config["follows"] == account["payload"]["config"]["follows"]
    assert config["preferences"]["locale"] == "en" and config["preferences"]["timezone"] == "Asia/Shanghai"
    assert config["link_overrides"][0]["url"] == VIDEO
    replace = {"expected_revision": 2, "mode": "replace", "config": {}}
    preview = client.post("/api/v1/me/config/import", json=replace).json()
    assert preview["removed"] == 2
    client.post(
        "/api/v1/me/config/import",
        json={**replace, "dry_run": False, "confirmation": preview["confirmation"]},
    ).raise_for_status()
    assert client.get("/api/v1/me/config/export").json()["follows"] == []


def test_import_unresolved_references_and_combined_limits_never_apply(document_stack):
    client, runtime, _, _ = document_stack
    login(client, runtime, followed=False)
    request = {"expected_revision": 0, "config": {"creators": [{"channel_id": "missing-channel"}]}}
    preview = client.post("/api/v1/me/config/import", json=request).json()
    assert preview["unresolved"] == ["missing-channel"]
    result = client.post(
        "/api/v1/me/config/import",
        json={**request, "dry_run": False, "confirmation": preview["confirmation"]},
    )
    assert result.status_code == 400 and result.json()["error"]["code"] == "UNRESOLVED_CONFIG"
    account = runtime.accounts.active(OWNER)["payload"]
    config = {
        **account["config"],
        "link_overrides": [
            {"event_key": "fixture:event:0", "url": f"https://www.espn.com/video/{index}", "state": "block"}
            for index in range(2000)
        ],
    }
    runtime.accounts.save_config(OWNER, config, 0)
    result = client.post(
        "/api/v1/me/config/import",
        json={
            "expected_revision": 1,
            "config": {"link_overrides": [{"event_key": "fixture:event:0", "url": VIDEO, "state": "pin"}]},
        },
    )
    assert result.status_code == 400 and result.json()["error"]["code"] == "CONFIG_LIMIT_EXCEEDED"
    assert runtime.accounts.active(OWNER)["payload"]["revision"] == 1
    result = client.post("/api/v1/events/e0/links", json=LINK)
    assert result.status_code == 400 and result.json()["error"]["code"] == "CONFIG_LIMIT_EXCEEDED"
    assert state(runtime, "link") == []


def large_config(runtime):
    config = deepcopy(runtime.accounts.active(OWNER)["payload"]["config"])
    config["link_overrides"] = [
        {
            "event_key": "fixture:event:0",
            "url": f"https://www.espn.com/video/{index}/" + "x" * 1200,
            "state": "block",
        }
        for index in range(2000)
    ]
    assert len(json.dumps(config).encode()) > 512_000
    return config


def test_large_config_exact_large_receipt_reopen_publish_and_rotate(document_stack):
    client, runtime, _, _ = document_stack
    address = login(client, runtime)
    original = client.get(address)
    config = large_config(runtime)
    request = {"expected_revision": 1, "mode": "replace", "config": config}
    preview = client.post("/api/v1/me/config/import", json=request)
    assert preview.status_code == 200, preview.text
    client.post(
        "/api/v1/me/config/import",
        json={**request, "dry_run": False, "confirmation": preview.json()["confirmation"]},
    ).raise_for_status()
    pk = owner_partition(OWNER)
    raw = runtime.store.get("state", pk, "account")
    assert "config_ref" in raw["payload"] and "config" not in raw["payload"]
    assert client.get("/api/v1/me/config/export").json() == config
    fresh = Runtime(LocalDocumentStore(runtime.store.path), runtime.cfg)
    assert fresh.accounts.active(OWNER)["payload"]["config"] == config
    command = SaveFollows(expected_revision=2, follows=config["follows"])
    result = fresh.save_follows(OWNER, command, "large-config-receipt")
    receipt = fresh.store.get("state", pk, "receipt:" + digest("large-config-receipt"))
    assert "result_ref" in receipt["payload"] and "result" not in receipt["payload"]
    drain(fresh)
    assert fresh.save_follows(OWNER, command, "large-config-receipt") == result
    assert client.get(address).content == original.content
    fresh.accounts.pause(OWNER, True)
    fresh.accounts.rotate(OWNER)
    fresh.accounts.pause(OWNER, False)
    drain(fresh)
    assert fresh.store.get("state", pk, "account")["payload"]["config_ref"] == raw["payload"]["config_ref"]
    published = fresh.publisher.read(fresh.accounts.address(OWNER)).body.encode()
    assert published == original.content
    # The store enforces actual serialized document/batch sizes on every write.
    assert all(
        len(json.dumps(row, ensure_ascii=True).encode()) < 512_000 for row in state(fresh, "value_chunk")
    )


@pytest.mark.parametrize("stage", ["fragment", "commit"])
def test_large_value_interruption_preserves_previous_account_feed_and_retry(
    document_stack, monkeypatch, stage
):
    client, runtime, _, _ = document_stack
    address = login(client, runtime)
    account, body, jobs = (
        runtime.accounts.active(OWNER),
        client.get(address).content,
        state(runtime, "outbox"),
    )
    config = large_config(runtime)
    batch = runtime.store.batch
    writes_seen = 0

    def fail(container, pk, writes):
        nonlocal writes_seen
        writes_seen += 1
        if (stage == "fragment" and writes_seen == 2) or (
            stage == "commit" and any(write.id == "account" for write in writes)
        ):
            raise StoreError("SYNTHETIC_INTERRUPTION", retryable=True)
        return batch(container, pk, writes)

    monkeypatch.setattr(runtime.store, "batch", fail)
    with pytest.raises(StoreError, match="INTERRUPTION"):
        runtime.accounts.save_config(OWNER, config, 1, key="large-failed-command")
    assert runtime.accounts.active(OWNER) == account
    assert client.get(address).content == body and state(runtime, "outbox") == jobs
    assert runtime.store.get("state", account["pk"], "receipt:" + digest("large-failed-command")) is None
    monkeypatch.setattr(runtime.store, "batch", batch)
    result = runtime.accounts.save_config(OWNER, config, 1, key="large-failed-command")
    assert result["config"] == config
    assert runtime.accounts.save_config(OWNER, config, 1, key="large-failed-command") == result


def test_incomplete_value_rejected_and_deleted_account_precedes_receipt_replay(document_stack):
    client, runtime, _, _ = document_stack
    login(client, runtime)
    config = large_config(runtime)
    runtime.accounts.save_config(OWNER, config, 1, key="large-deleted-command")
    pk = owner_partition(OWNER)
    raw = runtime.store.get("state", pk, "account")
    manifest = runtime.store.get("state", pk, "value:" + raw["payload"]["config_ref"])
    chunk = runtime.store.get("state", pk, manifest["payload"]["pieces"][0]["id"])
    corrupt = clean(chunk)
    corrupt["payload"]["text"] += "corrupt"
    runtime.store.batch("state", pk, [Write("replace", chunk["id"], corrupt, chunk["_etag"])])
    with pytest.raises(StoreError, match="VALUE_INCOMPLETE"):
        runtime.accounts.active(OWNER)
    tombstone = clean(raw)
    tombstone["payload"]["deleted"] = True
    runtime.store.batch("state", pk, [Write("replace", "account", tombstone, raw["_etag"])])
    with pytest.raises(HTTPException) as error:
        runtime.accounts.save_config(OWNER, config, 1, key="large-deleted-command")
    assert error.value.status_code == 403


def test_link_write_during_capture_cannot_publish_stale_links(document_stack, monkeypatch):
    client, runtime, _, _ = document_stack
    address = login(client, runtime)
    runtime.content.attach(OWNER, "e0", AddLink.model_validate(LINK), None)
    pk = owner_partition(OWNER)
    outbox = Outbox(runtime.store)
    claim = next(value for row in state(runtime, "outbox") if (value := outbox.claim(pk, row["id"])))
    original = client.get(address).content
    rows = runtime.content.rows

    def raced(uid):
        captured = rows(uid)
        monkeypatch.setattr(runtime.content, "rows", rows)
        runtime.content.override_link(uid, captured[0].id, "block")
        return captured

    monkeypatch.setattr(runtime.content, "rows", raced)
    with pytest.raises(StoreError, match="ACCOUNT_SNAPSHOT_CHANGED"):
        runtime.publish(claim)
    assert client.get(address).content == original
    runtime.publish(claim)
    assert VIDEO.encode() not in client.get(address).content


def test_unicode_values_survive_utf8_boundaries_and_wire_escaping(document_stack):
    _, runtime, _, _ = document_stack
    runtime.accounts.ensure(OWNER)
    value = {"text": '赛🧭\\"' * 60_000}
    values = runtime.accounts.values
    pk = owner_partition(OWNER)
    reference = values.pack(pk, "result", value)
    assert "result_ref" in reference
    assert values.unpack(pk, "result", reference) == value
    assert len(state(runtime, "value_chunk")) > 1
