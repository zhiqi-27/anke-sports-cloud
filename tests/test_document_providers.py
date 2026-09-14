from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier

import httpx
import pytest

from app import document_worker, provider_adapters
from app.document_accounts import Outbox, now
from app.document_runtime import Runtime
from app.document_store import LocalDocumentStore, StoreError, Write, clean, partition_items
from app.document_worker import LocalQueue, dispatch, run_job, schedule_calendar_window
from tests import test_document_runtime
from tests.test_document_runtime import drain, ics_rows

document_stack = test_document_runtime.document_stack


def f1_payload():
    date = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    return {
        "MRData": {
            "total": "1",
            "RaceTable": {
                "Races": [
                    {
                        "Circuit": {"circuitId": "fixture-circuit", "circuitName": "合成赛道"},
                        "raceName": "合成大奖赛",
                        "date": date,
                        "time": "12:00:00Z",
                        "FirstPractice": {"date": date},
                        "Qualifying": {"date": date, "time": "09:00:00Z"},
                    }
                ]
            },
        }
    }


@pytest.fixture
def fetched(monkeypatch):
    payload = f1_payload()
    monkeypatch.setattr(provider_adapters, "get_json", lambda *a, **kw: deepcopy(payload))
    return payload


def rows(runtime, kind="outbox", provider="jolpica"):
    return list(partition_items(runtime.store, "state", "provider:" + provider, kind))


def job(runtime, provider="jolpica"):
    state = runtime.providers.state(provider)
    return runtime.store.get("state", state["pk"], state["payload"]["pending_job_id"])


def message(row):
    return {"version": 1, "pk": row["pk"], "job_id": row["id"]}


def ready(runtime, provider="jolpica", *, clear_cooldown=True):
    state, pending = runtime.providers.state(provider), job(runtime, provider)
    reset = clean(pending)
    reset["due_at"] = now()
    writes = [Write("replace", pending["id"], reset, pending["_etag"])]
    if clear_cooldown:
        updated = clean(state)
        updated["payload"]["next_attempt_at"] = None
        writes.append(Write("replace", "sync", updated, state["_etag"]))
    runtime.store.batch("state", pending["pk"], writes)
    return message(pending)


def test_http_provider_refresh_follow_unchanged_reschedule_and_partial_failure(document_stack, fetched):
    client, runtime, _, _ = document_stack
    assert client.post("/api/v1/local/providers/jolpica/sync").status_code == 401
    client.post("/api/v1/auth/local").raise_for_status()
    for _ in range(2):
        assert client.post("/api/v1/local/providers/jolpica/sync").json() == {"queued": True}
    assert len([row for row in rows(runtime) if row["payload"]["operation"] == "provider_sync"]) == 1
    drain(runtime)
    status = client.get("/api/v1/status").json()["providers"][0]
    assert status["enabled"] and status["last_success"] and status["activity"] == "idle"
    assert client.get("/api/v1/sources?dataset=real").json()["items"][0]["id"] == "jolpica:f1"
    start = datetime.now(timezone.utc)
    schedule_range = {
        "from": (start - timedelta(days=1)).isoformat(),
        "to": (start + timedelta(days=2)).isoformat(),
        "dataset": "real",
    }
    assert (
        len(
            client.get("/api/v1/events", params={**schedule_range, "source_id": "jolpica:f1"}).json()["items"]
        )
        == 3
    )
    assert (
        client.get("/api/v1/events", params={**schedule_range, "source_id": "missing:team"}).json()["items"]
        == []
    )
    data = {"expected_revision": 0, "follows": [{"type": "competition", "source_key": "jolpica:f1"}]}
    preview = client.post("/api/v1/me/follows/preview", json=data).json()
    client.put(
        "/api/v1/me/follows", json={**data, "confirmation": preview["confirmation"]}
    ).raise_for_status()
    drain(runtime)
    address = client.get("/api/v1/me/feed/address").json()["url"]
    response = client.get(address)
    original = ics_rows(response)
    assert len(original) == 3
    assert sum("VALUE" in event["DTSTART"].params for event in original.values()) == 1
    before = runtime.store.get("state", "provider:jolpica", "schedule")["payload"]
    runtime.providers.enqueue("jolpica")
    drain(runtime)
    assert runtime.store.get("state", "provider:jolpica", "schedule")["payload"] == before
    assert client.get(address).content == response.content
    fetched["MRData"]["RaceTable"]["Races"][0]["time"] = "15:00:00Z"
    runtime.providers.enqueue("jolpica")
    drain(runtime)
    changed = client.get(address)
    after = ics_rows(changed)
    assert after.keys() == original.keys()
    assert sorted(int(after[uid]["SEQUENCE"]) - int(original[uid]["SEQUENCE"]) for uid in original) == [
        0,
        0,
        1,
    ]
    fetched["MRData"]["total"] = "2"
    runtime.providers.enqueue("jolpica")
    drain(runtime)
    failed = client.get("/api/v1/status").json()["providers"][0]
    assert failed["error"] == "INCOMPLETE_PAGINATION" and failed["activity"] == "waiting"
    assert client.get(address).content == changed.content
    assert client.get(address, headers={"If-None-Match": changed.headers["etag"]}).status_code == 304


def test_unknown_disabled_and_missing_key_are_honest_and_respect_cooldown(document_stack, monkeypatch):
    client, runtime, _, _ = document_stack
    assert runtime.providers.schedule() == 0  # Not enabled merely because a key exists.
    client.post("/api/v1/auth/local").raise_for_status()
    assert client.post("/api/v1/local/providers/unknown/sync").status_code == 400
    monkeypatch.setenv("BALLDONTLIE_API_KEY", "")
    monkeypatch.setattr(
        provider_adapters, "get_json", lambda *a, **kw: pytest.fail("No key must not make a request")
    )
    client.post("/api/v1/local/providers/balldontlie/sync").raise_for_status()
    drain(runtime)
    status = runtime.providers.statuses()[0]
    assert status["error"] == "PROVIDER_KEY_REQUIRED" and not status["enabled"]
    assert status["last_success"] is None and status["consecutive_failures"] == 1
    assert runtime.providers.enqueue("balldontlie")
    pending = job(runtime, "balldontlie")
    assert pending["state"] == "pending" and pending["due_at"] > now()
    assert not runtime.providers.enqueue("balldontlie")
    assert runtime.providers.schedule() == 0
    monkeypatch.setattr("app.document_api.local_allowed", lambda _: False)
    monkeypatch.setattr("app.document_api.firebase_subject", lambda _: "local-reviewer")
    assert (
        client.post(
            "/api/v1/local/providers/jolpica/sync", headers={"Authorization": "Bearer synthetic"}
        ).status_code
        == 404
    )


def test_manual_and_scheduled_refresh_single_flight_and_restart_deadline(document_stack, fetched):
    _, runtime, _, _ = document_stack
    runtime.providers.enqueue("jolpica")
    drain(runtime)
    assert Runtime(LocalDocumentStore(runtime.store.path), runtime.cfg).providers.schedule() == 0
    state = runtime.providers.state("jolpica")
    stale = clean(state)
    stale["payload"].update(
        last_success=(datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(), last_attempt_at=None
    )
    runtime.store.batch("state", state["pk"], [Write("replace", "sync", stale, state["_etag"])])
    barrier = Barrier(6)

    def enqueue(index):
        fresh = Runtime(LocalDocumentStore(runtime.store.path), runtime.cfg)
        barrier.wait(timeout=5)
        return fresh.providers.enqueue("jolpica", scheduled=bool(index % 2))

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(enqueue, range(6))) == 1
    assert (
        sum(
            row["state"] == "pending" and row["payload"]["operation"] == "provider_sync"
            for row in rows(runtime)
        )
        == 1
    )
    assert runtime.providers.schedule() == 0
    drain(runtime)
    assert runtime.providers.schedule() == 0


def test_failed_final_transaction_retains_schedule_success_time_and_no_partial_fanout(
    document_stack, fetched, monkeypatch
):
    _, runtime, _, _ = document_stack
    runtime.providers.enqueue("jolpica")
    drain(runtime)
    before = runtime.store.get("state", "provider:jolpica", "schedule")["payload"]
    succeeded = runtime.providers.state("jolpica")["payload"]["last_success"]
    changed_jobs = [row for row in rows(runtime) if row["payload"]["operation"] == "catalog_changed"]
    fetched["MRData"]["RaceTable"]["Races"][0]["time"] = "16:00:00Z"
    batch = runtime.store.batch

    def fail(container, pk, writes):
        if any(write.id == "schedule" for write in writes):
            # Fail a later operation inside the actual adapter transaction.
            writes = [
                Write(write.operation, write.id, write.body, '"stale"') if write.id == "sync" else write
                for write in writes
            ]
        return batch(container, pk, writes)

    monkeypatch.setattr(runtime.store, "batch", fail)
    runtime.providers.enqueue("jolpica")
    drain(runtime)
    assert runtime.store.get("state", "provider:jolpica", "schedule")["payload"] == before
    assert runtime.providers.state("jolpica")["payload"]["last_success"] == succeeded
    assert [row for row in rows(runtime) if row["payload"]["operation"] == "catalog_changed"] == changed_jobs
    assert job(runtime)["state"] == "pending"
    monkeypatch.setattr(runtime.store, "batch", batch)
    run_job(runtime, ready(runtime))
    assert (
        runtime.store.get("state", "provider:jolpica", "schedule")["payload"]["revision"]
        == before["revision"] + 1
    )


def test_expired_fetch_cannot_publish_after_new_lease_and_imported_id_is_preserved(
    document_stack, fetched, monkeypatch
):
    _, runtime, _, _ = document_stack
    normalized, sources = provider_adapters.fetch_schedule("jolpica")
    events = [
        {**row, "id": f"existing-sql-id-{index}", "updated_at": now()} for index, row in enumerate(normalized)
    ]
    runtime.catalog.publish("jolpica", events, sources, expected_revision=0, complete=True)
    old = runtime.store.get("state", "provider:jolpica", "schedule")["payload"]
    runtime.providers.enqueue("jolpica")
    pending = job(runtime)
    claim = Outbox(runtime.store).claim(pending["pk"], pending["id"])
    successor = []

    def lose_lease(provider):
        ready(runtime)
        successor.append(Outbox(runtime.store).claim(pending["pk"], pending["id"]))
        return deepcopy(normalized), deepcopy(sources)

    monkeypatch.setattr(runtime.providers, "fetch", lose_lease)
    with pytest.raises(StoreError, match="LEASE_LOST"):
        runtime.providers.process(claim)
    assert runtime.store.get("state", "provider:jolpica", "schedule")["payload"] == old
    assert runtime.providers.state("jolpica")["payload"]["last_success"] is None
    monkeypatch.setattr(runtime.providers, "fetch", lambda _: (deepcopy(normalized), deepcopy(sources)))
    runtime.providers.process(successor[0])
    assert {row.id for row in runtime.catalog.capture().events() if row.provider == "jolpica"} == {
        row["id"] for row in events
    }
    assert runtime.store.get("state", "provider:jolpica", "schedule")["payload"] == old


def test_long_retry_after_does_not_block_queue_and_early_wakes_do_not_fetch(document_stack, monkeypatch):
    _, runtime, _, _ = document_stack
    calls = []

    def rate_limited(provider):
        calls.append(provider)
        response = httpx.Response(
            429,
            request=httpx.Request("GET", "https://example.invalid/provider"),
            headers={"Retry-After": str(21 * 86400)},
        )
        response.raise_for_status()

    monkeypatch.setattr(runtime.providers, "fetch", rate_limited)
    runtime.providers.enqueue("jolpica")
    run_job(runtime, message(job(runtime)))
    pending = job(runtime)
    assert pending["payload"]["attempts"] == 1 and pending["state"] == "pending"
    assert pending["due_at"] < (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    cooldown = runtime.providers.state("jolpica")["payload"]["next_attempt_at"]
    assert cooldown > (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    queue = LocalQueue(runtime.store)
    assert dispatch(runtime.store, queue.send)
    run_job(runtime, ready(runtime, clear_cooldown=False))
    assert calls == ["jolpica"] and job(runtime)["payload"]["attempts"] == 1
    assert runtime.providers.state("jolpica")["payload"]["next_attempt_at"] == cooldown


def test_three_failures_open_circuit_recovery_resets_and_failure_commit_is_atomic(
    document_stack, monkeypatch
):
    _, runtime, _, _ = document_stack
    runtime.providers.enqueue("jolpica")

    def unavailable(_):
        raise httpx.ConnectError("fixture private detail must not be saved")

    monkeypatch.setattr(runtime.providers, "fetch", unavailable)
    for attempt in range(3):
        run_job(runtime, message(job(runtime)) if attempt == 0 else ready(runtime))
    state = runtime.providers.state("jolpica")
    assert state["payload"]["consecutive_failures"] == 3
    assert state["payload"]["error"] == "UPSTREAM_NETWORK_ERROR"
    assert (
        state["payload"]["next_attempt_at"] > (datetime.now(timezone.utc) + timedelta(minutes=14)).isoformat()
    )
    payload = f1_payload()
    monkeypatch.setattr(
        runtime.providers,
        "fetch",
        lambda _: provider_adapters.fetch_schedule("jolpica", request_json=lambda *a, **k: payload),
    )
    run_job(runtime, ready(runtime))
    assert runtime.providers.state("jolpica")["payload"]["consecutive_failures"] == 0
    runtime.providers.enqueue("jolpica")
    batch = runtime.store.batch

    def failed_storage(container, pk, writes):
        if any(write.id == "sync" and write.body["payload"]["error"] for write in writes):
            raise StoreError("SYNTHETIC_STORAGE_OUTAGE", retryable=True)
        return batch(container, pk, writes)

    monkeypatch.setattr(runtime.store, "batch", failed_storage)
    monkeypatch.setattr(runtime.providers, "fetch", unavailable)
    with pytest.raises(StoreError, match="STORAGE_OUTAGE"):
        run_job(runtime, message(job(runtime)))
    assert job(runtime)["state"] == "running"  # No job-only fallback acknowledgment.
    assert runtime.providers.state("jolpica")["payload"]["error"] == ""


def test_local_and_azure_window_scheduling_share_daily_identity_and_startup_recovers(
    document_stack, fetched, monkeypatch
):
    _, runtime, _, _ = document_stack
    assert schedule_calendar_window(runtime)
    assert not schedule_calendar_window(runtime)
    assert schedule_calendar_window(runtime, instant=datetime.now(timezone.utc) + timedelta(days=1))
    runtime.providers.enqueue("jolpica")

    @contextmanager
    def context():
        yield Runtime(LocalDocumentStore(runtime.store.path), runtime.cfg)

    monkeypatch.setattr(document_worker, "runtime_context", context)
    monkeypatch.setattr(document_worker, "settings", lambda: runtime.cfg)
    for _ in range(12):
        assert document_worker.main(["--once"]) == 0
    assert runtime.providers.state("jolpica")["payload"]["last_success"]
    assert len([row for row in rows(runtime) if row["payload"]["operation"] == "provider_sync"]) == 1
    assert len(rows(runtime, provider="calendar-window")) == 2


def test_basketball_pagination_date_precision_and_metadata_failure():
    def team(ident):
        return {"id": ident, "full_name": f"Team {ident}", "abbreviation": str(ident)}

    games = [
        {
            "id": i,
            "date": "2026-09-12",
            "datetime": "2026-09-12T10:00:00Z" if i else None,
            "status": "Final" if i else "TBD",
            "visitor_team": team(1),
            "home_team": team(2),
        }
        for i in range(2)
    ]
    calls = []

    def request(client, url, **kwargs):
        if url.endswith("/teams"):
            return {
                "data": [
                    {**team(1), "conference": "East", "division": "Atlantic"},
                    {**team(3), "conference": "East", "division": "Atlantic"},
                ]
            }
        calls.append(dict(kwargs["params"]))
        if kwargs["params"].get("season_type") == "preseason":
            return {"data": [], "meta": {}}
        index = int("cursor" in kwargs["params"])
        return {"data": [games[index]], "meta": {"next_cursor": 10} if index == 0 else {"per_page": 100}}

    events, sources = provider_adapters.fetch_schedule(
        "balldontlie", request_json=request, key_reader=lambda _: "fixture-key"
    )
    assert len(events) == 2 and len(sources) == 3
    assert {source["id"] for source in sources} == {
        "balldontlie:nba",
        "balldontlie:team:1",
        "balldontlie:team:3",
    }
    assert events[0]["participants"][1]["id"] == "balldontlie:team:2"
    assert provider_adapters.source_logo_url("balldontlie:team:14", "LAL") == (
        "https://cdn.nba.com/logos/nba/1610612747/primary/L/logo.svg"
    )
    assert provider_adapters.source_logo_url("balldontlie:team:1", "FIX") is None
    assert provider_adapters.source_logo_url("football-data:team:64", "LIV") == (
        "https://crests.football-data.org/64.png"
    )
    assert calls[1]["cursor"] == 10
    assert events[0]["time_precision"] == "date_only" and events[1]["status"] == "finished"
    with pytest.raises(KeyError):
        provider_adapters.fetch_schedule(
            "balldontlie", request_json=lambda *a, **kw: {"data": games}, key_reader=lambda _: "fixture-key"
        )
    with pytest.raises(ValueError, match="PAGINATION_LOOP"):
        provider_adapters.fetch_schedule(
            "balldontlie",
            request_json=lambda *a, **kw: {"data": [games[0]], "meta": {"next_cursor": 10}},
            key_reader=lambda _: "fixture-key",
        )


def test_football_count_status_and_unknown_time_are_preserved():
    def match(index, status, time):
        return {
            "id": index,
            "status": status,
            "utcDate": time,
            "awayTeam": {"id": 1, "name": "Away"},
            "homeTeam": {"id": 2, "name": "Home"},
        }

    matches = [
        match(1, "SCHEDULED", "2026-09-12T00:00:00Z"),
        match(2, "POSTPONED", None),
        match(3, "CANCELLED", "2026-09-12T12:00:00Z"),
    ]
    payload = {"matches": matches, "resultSet": {"count": 3}}

    def request(client, url, **kwargs):
        if url.endswith("/teams"):
            return {
                "season": {"startDate": "2026-08-01"},
                "count": 1,
                "teams": [
                    {
                        "id": 3,
                        "name": "No fixtures yet",
                        "crest": "https://crests.football-data.org/3.png",
                    }
                ],
            }
        assert kwargs["params"] == {"season": "2026"}
        return payload

    events, sources = provider_adapters.fetch_schedule(
        "football-data", request_json=request, key_reader=lambda _: "fixture-key"
    )
    assert next(row for row in sources if row["id"] == "football-data:team:3")["logo_url"] == (
        "https://crests.football-data.org/3.png"
    )
    assert [(r["time_precision"], r["status"]) for r in events] == [
        ("date_only", "scheduled"),
        ("unknown", "postponed"),
        ("exact", "cancelled"),
    ]
    payload["resultSet"]["count"] = 4
    with pytest.raises(ValueError, match="INCOMPLETE"):
        provider_adapters.fetch_schedule(
            "football-data", request_json=request, key_reader=lambda _: "fixture-key"
        )


def test_fetch_deadline_and_duplicate_identity_reject_whole_batch(monkeypatch):
    times = iter([0, 181])
    monkeypatch.setattr(provider_adapters, "monotonic", lambda: next(times))
    with pytest.raises(ValueError, match="DEADLINE"):
        provider_adapters.fetch_schedule(
            "jolpica", request_json=lambda *a, **k: pytest.fail("deadline exceeded")
        )
    monkeypatch.undo()
    payload = f1_payload()
    payload["MRData"]["RaceTable"]["Races"] *= 2
    payload["MRData"]["total"] = "2"
    with pytest.raises(ValueError, match="AMBIGUOUS_CIRCUIT"):
        provider_adapters.fetch_schedule("jolpica", request_json=lambda *a, **k: payload)


def test_configured_provider_can_bootstrap_without_local_http(document_stack, fetched):
    _, runtime, _, _ = document_stack
    configured = Runtime(
        runtime.store, runtime.cfg.model_copy(update={"enabled_sports_providers": ["jolpica"]})
    )
    assert configured.providers.schedule() == 1
    assert configured.providers.schedule() == 0
    drain(configured)
    assert configured.providers.state("jolpica")["payload"]["enabled"]
    assert configured.providers.schedule() == 0


def test_deployed_policy_disables_queued_fetch_without_removing_old_schedule(
    document_stack, fetched, monkeypatch
):
    _, runtime, _, _ = document_stack
    runtime.providers.enqueue("jolpica")
    drain(runtime)
    original = runtime.store.get("state", "provider:jolpica", "schedule")["payload"]
    runtime.providers.enqueue("jolpica")
    # Exercise the environment policy with a local adapter, not a staging deployment.
    disabled = Runtime(
        runtime.store, runtime.cfg.model_copy(update={"env": "staging", "enabled_sports_providers": []})
    )
    monkeypatch.setattr(disabled.providers, "fetch", lambda _: pytest.fail("disabled source must not fetch"))
    assert disabled.providers.schedule() == 0
    assert disabled.providers.statuses()[0]["enabled"] is False
    assert disabled.providers.statuses()[0]["error"] == "PROVIDER_DISABLED"
    run_job(disabled, message(job(disabled)))
    assert runtime.store.get("state", "provider:jolpica", "schedule")["payload"] == original
    assert disabled.providers.statuses()[0]["error"] == "PROVIDER_DISABLED"
    assert not disabled.providers.state("jolpica")["payload"]["enabled"]


def test_provider_configuration_accepts_only_known_sources():
    from pydantic import ValidationError
    from app.config import Settings

    assert Settings(_env_file=None, enabled_sports_providers=["jolpica"]).enabled_sports_providers == [
        "jolpica"
    ]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, enabled_sports_providers=["arbitrary-url"])


def test_nba_catalog_without_games_and_free_tier_pacing(monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(provider_adapters, "monotonic", lambda: clock[0])
    monkeypatch.setattr(provider_adapters, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def request(client, url, **kwargs):
        calls.append(clock[0])
        if url.endswith("/teams"):
            return {
                "data": [
                    {
                        "id": 1,
                        "full_name": "Fixture team",
                        "abbreviation": "FIX",
                        "conference": "East",
                        "division": "Atlantic",
                    }
                ]
            }
        return {"data": [], "meta": {"next_cursor": None}}

    monkeypatch.setattr(provider_adapters, "get_json", request)
    events, sources = provider_adapters.fetch_schedule("balldontlie", key_reader=lambda _: "fixture")
    assert events == []
    assert {s["id"] for s in sources} == {"balldontlie:nba", "balldontlie:team:1"}
    assert calls == [0, 13, 26]


def test_nba_preseason_is_explicitly_fetched_and_labeled():
    calls = []
    team = {"id": 1, "full_name": "San Antonio Spurs", "abbreviation": "SAS"}

    def request(client, url, **kwargs):
        if url.endswith("/teams"):
            return {"data": []}
        calls.append(dict(kwargs["params"]))
        if kwargs["params"].get("season_type") != "preseason":
            return {"data": [], "meta": {}}
        return {
            "data": [
                {
                    "id": 7,
                    "date": "2026-10-08",
                    "datetime": "2026-10-08T00:00:00Z",
                    "status_state": "scheduled",
                    "visitor_team": {
                        **team,
                        "id": 2,
                        "full_name": "Oklahoma City Thunder",
                        "abbreviation": "OKC",
                    },
                    "home_team": team,
                }
            ],
            "meta": {},
        }

    events, _ = provider_adapters.fetch_schedule(
        "balldontlie", request_json=request, key_reader=lambda _: "fixture"
    )
    assert [call.get("season_type") for call in calls] == [None, "preseason"]
    assert len(events) == 1
    assert events[0]["title"] == "[季前赛] Oklahoma City Thunder @ San Antonio Spurs"
    assert events[0]["source_key"] == "balldontlie:game:7"


@pytest.mark.parametrize(
    "state,expected",
    [
        ("postponed", "postponed"),
        ("suspended", "postponed"),
        ("delayed", "postponed"),
        ("canceled", "cancelled"),
        ("final", "finished"),
    ],
)
def test_nba_structured_status(state, expected):
    team = {"id": 1, "full_name": "Fixture", "abbreviation": "FIX"}

    def request(client, url, **kwargs):
        if url.endswith("/teams"):
            return {"data": []}
        if kwargs["params"].get("season_type") == "preseason":
            return {"data": [], "meta": {}}
        return {
            "data": [
                {
                    "id": 1,
                    "date": "2026-09-12",
                    "status_state": state,
                    "visitor_team": team,
                    "home_team": {**team, "id": 2},
                }
            ],
            "meta": {},
        }

    events, _ = provider_adapters.fetch_schedule(
        "balldontlie", request_json=request, key_reader=lambda _: "fixture"
    )
    assert events[0]["status"] == expected
