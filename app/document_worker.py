"""Durable Change Feed -> Queue delivery and shared leased job handlers."""

from contextlib import contextmanager
import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import re
import time
from uuid import uuid4

from app.config import settings
from app.document_accounts import Outbox, document, now, projection_job
from app.document_runtime import Runtime
from app.document_store import Conflict, StoreError, Write, clean, open_document_store, partition_items
from app.security import digest


def envelope(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "pk", "job_id"}
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise StoreError("QUEUE_MESSAGE_INVALID")
    if not isinstance(value["pk"], str) or not re.fullmatch(
        r"user:[a-f0-9]{64}|provider:[a-z0-9][a-z0-9_-]{0,39}", value["pk"]
    ):
        raise StoreError("QUEUE_MESSAGE_INVALID")
    if not isinstance(value["job_id"], str) or not re.fullmatch(r"job:[a-f0-9]{32}", value["job_id"]):
        raise StoreError("QUEUE_MESSAGE_INVALID")
    return value


def dispatch(store, send):
    """Advance only after every send; ambiguous sends safely replay the same job.

    Cursor/lease lives in indexes, outside the observed state Change Feed. The
    caller supplies an isolated Cosmos client to protect SDK continuation state.
    """
    pk, ident, instant = "dispatch", "outbox", datetime.now(timezone.utc)
    previous = store.get("indexes", pk, ident)
    if previous and previous["payload"].get("lease_until", "") > instant.isoformat():
        return False
    checkpoint = document(
        pk,
        ident,
        "dispatch_checkpoint",
        cursor=previous["payload"]["cursor"] if previous else None,
        lease=uuid4().hex,
        lease_until=(instant + timedelta(minutes=5)).isoformat(),
    )
    try:
        store.batch(
            "indexes",
            pk,
            [
                Write(
                    "replace" if previous else "create",
                    ident,
                    checkpoint,
                    previous["_etag"] if previous else None,
                )
            ],
        )
    except Conflict:
        return False
    claimed = store.get("indexes", pk, ident)
    if claimed["payload"]["lease"] != checkpoint["payload"]["lease"]:
        return False
    cursor = claimed["payload"]["cursor"]
    try:
        rows, advanced = store.changes("state", cursor, limit=100)
        for row in rows:
            if row["kind"] != "outbox" or row["state"] not in {"pending", "running"}:
                continue
            message = envelope({"version": 1, "pk": row["pk"], "job_id": row["id"]})
            delay = max(
                0,
                math.ceil(
                    (datetime.fromisoformat(row["due_at"]) - datetime.now(timezone.utc)).total_seconds()
                ),
            )
            if delay > 7 * 86400:
                raise StoreError("QUEUE_DELAY_OUT_OF_RANGE", retryable=True)
            send(json.dumps(message), delay)
        if claimed["payload"]["lease_until"] <= now():
            raise StoreError("DISPATCH_LEASE_LOST", retryable=True)
        completed = clean(claimed)
        completed["payload"] = {"cursor": advanced, "lease": None, "lease_until": ""}
        store.batch("indexes", pk, [Write("replace", ident, completed, claimed["_etag"])])
        return advanced != cursor
    except Exception:
        released = clean(claimed)
        released["payload"] = {"cursor": cursor, "lease": None, "lease_until": ""}
        try:
            store.batch("indexes", pk, [Write("replace", ident, released, claimed["_etag"])])
        except StoreError:
            pass  # A newer dispatcher owns the cursor; never overwrite it.
        raise


def fanout(runtime, claim):
    store, outbox = runtime.store, Outbox(runtime.store)
    job = outbox.current(claim)
    routes = store.page("indexes", "owners", "owner_route", after=job["payload"].get("after", ""), limit=100)
    for route in routes:
        pk = route["payload"]["owner_pk"]
        account = store.get("state", pk, "account")
        if not account or account["payload"]["deleted"]:
            continue
        pending = projection_job(pk, account["payload"]["revision"])
        pending["id"] = "job:" + digest(claim["pk"] + ":" + claim["id"] + ":" + pk)[:32]
        if store.get("state", pk, pending["id"]):
            continue
        store.batch(
            "state",
            pk,
            [
                Write("replace", "account", clean(account), account["_etag"]),
                Write("create", pending["id"], pending),
            ],
        )
    current = outbox.current(claim)
    if len(routes) == 100:
        updated = clean(current)
        updated.update(state="pending", due_at=now())
        updated["payload"] = {**current["payload"], "after": routes[-1]["id"], "lease": None, "attempts": 0}
        write = Write("replace", current["id"], updated, current["_etag"])
    else:
        write = outbox.completion(claim)
    store.batch("state", claim["pk"], [write])


def run_job(runtime, message):
    value = envelope(message)
    outbox = Outbox(runtime.store)
    claim = outbox.claim(value["pk"], value["job_id"])
    if not claim:
        return False
    if claim["payload"]["operation"] == "provider_sync":
        # This handler owns the atomic provider state + failure/success record.
        # A persistence failure must propagate, never fall back to job-only failure.
        runtime.providers.process(claim)
        return True
    try:
        operation = claim["payload"]["operation"]
        if operation == "projection" and claim["pk"].startswith("user:"):
            runtime.publish(claim)
        elif operation == "catalog_changed" and claim["pk"].startswith("provider:"):
            fanout(runtime, claim)
        else:
            raise StoreError("DOCUMENT_JOB_NOT_MIGRATED")
    except Exception as error:
        outbox.fail(claim, error)
    return True


class LocalQueue:
    """Explicit persistent local substitute; not Azurite or Azure Queue evidence."""

    def __init__(self, store):
        self.store = store

    def send(self, body, delay):
        value = envelope(json.loads(body))
        row = document("local:queue", uuid4().hex, "local_message", message=value)
        row["due_at"] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        self.store.batch("indexes", row["pk"], [Write("create", row["id"], row)])

    def consume(self, runtime):
        for row in partition_items(self.store, "indexes", "local:queue", "local_message"):
            if row["due_at"] <= now():
                run_job(runtime, row["payload"]["message"])
                try:
                    self.store.batch("indexes", row["pk"], [Write("delete", row["id"], etag=row["_etag"])])
                except Conflict:
                    pass
                return True
        return False


@contextmanager
def runtime_context():
    cfg = settings()
    store = open_document_store(cfg)
    try:
        yield Runtime(store, cfg)
    finally:
        store.close()


def schedule_calendar_window(runtime, *, instant=None):
    instant = instant or datetime.now(timezone.utc)
    job = projection_job("provider:calendar-window", 0)
    job["id"] = "job:" + digest("calendar-window:" + instant.date().isoformat())[:32]
    job["payload"]["operation"] = "catalog_changed"
    if runtime.store.get("state", job["pk"], job["id"]):
        return False
    try:
        runtime.store.batch("state", job["pk"], [Write("create", job["id"], job)])
    except Conflict:
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if settings().storage_backend != "documents-local":
        raise StoreError("LOCAL_DOCUMENT_WORKER_REQUIRES_LOCAL_ADAPTER")
    with runtime_context() as runtime:
        queue = LocalQueue(runtime.store)
        next_schedule = 0
        while True:
            healthy, advanced, consumed = True, False, False
            try:
                if time.monotonic() >= next_schedule:
                    runtime.providers.schedule()
                    schedule_calendar_window(runtime)
                    next_schedule = time.monotonic() + 60
                advanced = dispatch(runtime.store, queue.send)
                consumed = queue.consume(runtime)
            except Exception as error:
                code = error.code if isinstance(error, StoreError) else "DOCUMENT_WORKER_CYCLE_FAILED"
                logging.warning("DOCUMENT_WORKER_CYCLE_FAILED code=%s", code)
                healthy = False
            if args.once:
                return 0 if healthy else 1
            if not advanced and not consumed:
                time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
