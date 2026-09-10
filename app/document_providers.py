"""Fenced provider refreshes, atomic schedule publication and persisted cadence."""

from datetime import datetime, timedelta, timezone

from app.document_accounts import Outbox, document, now, projection_job
from app.document_catalog import Snapshot, provider_partition
from app.document_store import Conflict, StoreError, Write, clean
from app.job_rules import error_code, retry_seconds
from app.provider_adapters import PROVIDERS, PROVIDER_REFRESH, fetch_schedule, provider_key
from app.security import digest


def due_at(value, instant):
    # Azure Queue visibility must be shorter than its 7-day message lifetime.
    # Long upstream cooldowns remain authoritative in sync.next_attempt_at;
    # early queue wakes defer again without fetching or charging an attempt.
    return min(value, (instant + timedelta(days=6)).isoformat())


class Providers:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        self.outbox = Outbox(self.store)

    def state(self, provider):
        if provider not in PROVIDERS:
            raise StoreError("UNKNOWN_PROVIDER")
        return self.store.get("state", provider_partition(provider), "sync")

    def enqueue(self, provider, *, scheduled=False, configured=False, instant=None):
        instant = instant or datetime.now(timezone.utc)
        stamp, cutoff = instant.isoformat(), (instant - PROVIDER_REFRESH).isoformat()
        old = self.state(provider)
        pk = provider_partition(provider)
        value = (
            dict(old["payload"])
            if old
            else dict(
                id=provider,
                last_success=None,
                error="",
                enabled=False,
                consecutive_failures=0,
                next_attempt_at=None,
                last_attempt_at=None,
                pending_job_id=None,
            )
        )
        if scheduled and (
            not (value["enabled"] or configured)
            or (value["last_success"] or "") > cutoff
            or (value["last_attempt_at"] or "") > cutoff
            or (value["next_attempt_at"] or "") > stamp
        ):
            return False
        pending_id = value["pending_job_id"]
        if pending_id:
            pending = self.store.get("state", pk, pending_id)
            if not pending:
                raise StoreError("PROVIDER_JOB_INCOMPLETE", retryable=True)
            if pending["state"] in {"pending", "running"}:
                return False
        job = projection_job(pk, 0)
        job["payload"].update(operation="provider_sync", provider=provider)
        job["due_at"] = due_at(max(stamp, value["next_attempt_at"] or stamp), instant)
        value["pending_job_id"] = job["id"]
        state = document(pk, "sync", "provider_state", **value)
        try:
            self.store.batch(
                "state",
                pk,
                [
                    Write("replace" if old else "create", "sync", state, old["_etag"] if old else None),
                    Write("create", job["id"], job),
                ],
            )
        except Conflict:
            current = self.state(provider)
            other = (
                self.store.get("state", pk, current["payload"]["pending_job_id"])
                if current and current["payload"]["pending_job_id"]
                else None
            )
            if other and other["state"] in {"pending", "running"}:
                return False
            raise
        return True

    def schedule(self, *, instant=None):
        configured = set(self.runtime.cfg.enabled_sports_providers)
        return sum(
            self.enqueue(provider, scheduled=True, configured=provider in configured, instant=instant)
            for provider in sorted(PROVIDERS)
            if self.runtime.cfg.env == "local" or provider in configured
        )

    def statuses(self):
        instant = now()
        result = []
        for provider in sorted(PROVIDERS):
            state = self.state(provider)
            if not state:
                continue
            value = state["payload"]
            job = (
                self.store.get("state", state["pk"], value["pending_job_id"])
                if value["pending_job_id"]
                else None
            )
            activity = "idle"
            error = value["error"]
            if value["pending_job_id"] and not job:
                error = "PROVIDER_JOB_INCOMPLETE"
            elif job and job["state"] in {"pending", "running"}:
                activity = (
                    "running"
                    if job["state"] == "running" and job["due_at"] > instant
                    else "waiting"
                    if job["due_at"] > instant
                    else "queued"
                )
            elif job and job["state"] == "failed":
                error = job["payload"]["error"] or error
            allowed = self.runtime.cfg.env == "local" or provider in self.runtime.cfg.enabled_sports_providers
            if not allowed:
                error = "PROVIDER_DISABLED"
            result.append(
                {
                    "id": provider,
                    "last_success": value["last_success"],
                    "error": error,
                    "enabled": value["enabled"] and allowed,
                    "consecutive_failures": value["consecutive_failures"],
                    "next_attempt_at": value["next_attempt_at"],
                    "activity": activity,
                }
            )
        return result

    def owned(self, claim):
        provider = claim["payload"].get("provider")
        if provider not in PROVIDERS or claim["pk"] != provider_partition(provider):
            raise StoreError("PROVIDER_JOB_INVALID")
        self.outbox.current(claim)
        state = self.state(provider)
        if not state or state["payload"]["pending_job_id"] != claim["id"]:
            raise StoreError("PROVIDER_LEASE_LOST", retryable=True)
        return state

    def fetch(self, provider):
        return fetch_schedule(provider, key_reader=lambda name: provider_key(name, self.runtime.cfg))

    def process(self, claim):
        provider = claim["payload"].get("provider")
        state = self.owned(claim)
        if self.runtime.cfg.env != "local" and provider not in self.runtime.cfg.enabled_sports_providers:
            disabled = clean(state)
            disabled["payload"].update(
                enabled=False, pending_job_id=None, error="PROVIDER_DISABLED", next_attempt_at=None
            )
            self.store.batch(
                "state",
                state["pk"],
                [
                    Write("replace", "sync", disabled, state["_etag"]),
                    self.outbox.completion(claim),
                ],
            )
            return
        instant = datetime.now(timezone.utc)
        cooldown = state["payload"]["next_attempt_at"]
        if cooldown and cooldown > instant.isoformat():
            job = self.outbox.current(claim)
            deferred = clean(job)
            deferred.update(state="pending", due_at=due_at(cooldown, instant))
            deferred["payload"].update(lease=None, attempts=max(0, job["payload"]["attempts"] - 1))
            self.store.batch(
                "state",
                state["pk"],
                [
                    Write("replace", "sync", clean(state), state["_etag"]),
                    Write("replace", job["id"], deferred, job["_etag"]),
                ],
            )
            return
        attempted = clean(state)
        attempted["payload"]["last_attempt_at"] = instant.isoformat()
        job = self.outbox.current(claim)
        self.store.batch(
            "state",
            state["pk"],
            [
                Write("replace", "sync", attempted, state["_etag"]),
                Write("replace", job["id"], clean(job), job["_etag"]),
            ],
        )
        state = self.owned(claim)
        old = self.store.get("state", state["pk"], "schedule")
        revision = old["payload"]["revision"] if old else 0
        try:
            events, sources = self.fetch(provider)
            previous = {
                row.source_key: row.id
                for row in Snapshot(self.runtime.catalog, [old] if old else []).events()
            }
            rows = [
                {
                    **row,
                    "id": previous.get(row["source_key"], digest("provider-event:" + row["source_key"])[:32]),
                    "updated_at": now(),
                }
                for row in events
            ]

            def finalize():
                current = self.owned(claim)
                if current["_etag"] != state["_etag"]:
                    raise StoreError("PROVIDER_LEASE_LOST", retryable=True)
                completed = clean(current)
                completed["payload"].update(
                    last_success=now(),
                    enabled=True,
                    error="",
                    consecutive_failures=0,
                    next_attempt_at=None,
                    pending_job_id=None,
                )
                return [Write("replace", "sync", completed, current["_etag"]), self.outbox.completion(claim)]

            self.runtime.catalog.publish(
                provider, rows, sources, expected_revision=revision, complete=True, finalize=finalize
            )
        except Exception as error:
            self.fail(claim, state, error)

    def fail(self, claim, expected_state, error):
        # Never acknowledge a failed persistence attempt. Azure or the persisted
        # lease wake will retry; only a successful atomic failure record is final.
        state = self.owned(claim)
        if state["_etag"] != expected_state["_etag"]:
            raise StoreError("PROVIDER_LEASE_LOST", retryable=True)
        job = self.outbox.current(claim)
        instant = datetime.now(timezone.utc)
        code = error.code if isinstance(error, StoreError) else error_code(error)
        failures = state["payload"]["consecutive_failures"] + 1
        delay = retry_seconds(error, job["payload"]["attempts"], instant)
        if isinstance(error, StoreError) and error.retry_after:
            delay = max(delay, min(error.retry_after, 30 * 86400))
        missing_key = code.endswith("KEY_REQUIRED")
        if failures >= 3 or missing_key:
            delay = max(delay, 6 * 3600 if missing_key else min(6 * 3600, 900 * 2 ** min(failures - 3, 5)))
        deadline = (instant + timedelta(seconds=delay)).isoformat()
        failed = clean(state)
        failed["payload"].update(consecutive_failures=failures, error=code, next_attempt_at=deadline)
        updated = clean(job)
        terminal = missing_key or job["payload"]["attempts"] >= 5
        updated.update(state="failed" if terminal else "pending", due_at=due_at(deadline, instant))
        updated["payload"].update(lease=None, error=code)
        if terminal:
            updated["finished_at"] = instant.isoformat()
        self.store.batch(
            "state",
            state["pk"],
            [
                Write("replace", "sync", failed, state["_etag"]),
                Write("replace", job["id"], updated, job["_etag"]),
            ],
        )
