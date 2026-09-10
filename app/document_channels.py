"""One shared, fenced discovery pipeline per channel, each step commits before the next HTTP."""

from datetime import datetime, timedelta, timezone
import re

from fastapi import HTTPException

from app.document_accounts import Outbox, document, now, projection_job
from app.document_store import Conflict, StoreError, Write, clean, partition_items
from app.document_values import Values
from app.job_rules import error_code, retry_seconds
from app.youtube_content_rules import uploads_page, video_batch
from app.youtube_rules import WAIT_CODES
from app.youtube_transport import CHANNEL_ID, channel_details, items


def channel_partition(ident):
    if not isinstance(ident, str) or not re.fullmatch(CHANNEL_ID, ident):
        raise StoreError("CHANNEL_ID_INVALID")
    return "channel:" + ident


def next_job(outbox, claim, **payload):
    old = outbox.current(claim)
    updated = clean(old)
    updated.update(state="pending", due_at=now())
    updated["payload"].update(payload, attempts=0, lease=None, error="")
    return Write("replace", old["id"], updated, old["_etag"])


class Channels:
    def __init__(self, runtime):
        self.rt, self.store = runtime, runtime.store
        self.outbox, self.values = Outbox(self.store), Values(self.store)

    def get(self, ident):
        return self.store.get("state", channel_partition(ident), "channel")

    def ensure(self, details):
        ident = details["channel_id"]
        pk = channel_partition(ident)
        route = document("channels", ident, "channel_route", channel_id=ident)
        try:
            self.store.batch("indexes", "channels", [Write("create", ident, route)])
        except Conflict:
            pass
        old = self.get(ident)
        if not old:
            row = document(
                pk,
                "channel",
                "channel",
                **details,
                updated_at=now(),
                last_success=None,
                error="",
                next_poll_at=None,
                retry_at=None,
                failures=0,
                pending_job_id=None,
            )
            try:
                self.store.batch("state", pk, [Write("create", "channel", row)])
            except Conflict:
                pass
        # Existing metadata changes only under the shared pipeline lease.
        return self.get(ident)

    def register(self, ident, owner_pk):
        pk = channel_partition(ident)
        row = document(pk, owner_pk, "channel_owner", owner_pk=owner_pk)
        try:
            self.store.batch("indexes", pk, [Write("create", owner_pk, row)])
        except Conflict:
            pass

    def interested(self, ident):
        for route in partition_items(self.store, "indexes", channel_partition(ident), "channel_owner"):
            account = self.store.get("state", route["payload"]["owner_pk"], "account")
            if account and not account["payload"]["deleted"]:
                config = self.rt.accounts.active(account["payload"]["user_id"])["payload"]["config"]
                if any(row["channel_id"] == ident and row["enabled"] for row in config["creators"]):
                    return True
        return False

    def enqueue(self, ident, *, scheduled=False):
        old = self.get(ident)
        if not old:
            raise StoreError("CHANNEL_NOT_FOUND")
        value = old["payload"]
        if scheduled and value["next_poll_at"] and value["next_poll_at"] > now():
            return False
        if not self.interested(ident):
            return False
        if value["pending_job_id"]:
            pending = self.store.get("state", old["pk"], value["pending_job_id"])
            if not pending:
                raise StoreError("CHANNEL_JOB_INCOMPLETE", retryable=True)
            if pending["state"] in {"pending", "running"}:
                return False
        job = projection_job(old["pk"], 0)
        job["payload"].update(
            operation="channel_sync",
            channel_id=ident,
            stage="poll",
            seen=[],
            cursor=None,
            cutoff=(datetime.now(timezone.utc) - timedelta(days=14)).isoformat(),
            cycle_started=now(),
        )
        if value["retry_at"]:
            job["due_at"] = min(
                max(now(), value["retry_at"]), (datetime.now(timezone.utc) + timedelta(days=6)).isoformat()
            )
        updated = clean(old)
        updated["payload"]["pending_job_id"] = job["id"]
        try:
            self.store.batch(
                "state",
                old["pk"],
                [Write("replace", "channel", updated, old["_etag"]), Write("create", job["id"], job)],
            )
        except Conflict:
            current = self.get(ident)
            pending = (
                self.store.get("state", old["pk"], current["payload"]["pending_job_id"])
                if (current and current["payload"]["pending_job_id"])
                else None
            )
            if pending and pending["state"] in {"pending", "running"}:
                return False
            raise
        return True

    def video(self, ident, video_id):
        row = self.store.get("state", channel_partition(ident), "video:" + video_id)
        if not row:
            return None
        return self.values.get(row["pk"], row["payload"]["value_ref"])

    def owned(self, claim):
        self.outbox.current(claim)
        ident = claim["payload"]["channel_id"]
        old = self.get(ident)
        if (
            claim["pk"] != channel_partition(ident)
            or not old
            or old["payload"]["pending_job_id"] != claim["id"]
        ):
            raise StoreError("CHANNEL_LEASE_LOST", retryable=True)
        return old

    def process(self, claim):
        old = self.owned(claim)
        if not self.interested(old["payload"]["channel_id"]):
            updated = clean(old)
            updated["payload"]["pending_job_id"] = None
            self.store.batch(
                "state",
                old["pk"],
                [Write("replace", "channel", updated, old["_etag"]), self.outbox.completion(claim)],
            )
            return
        if old["payload"]["retry_at"] and old["payload"]["retry_at"] > now():
            self.wait(claim, old, old["payload"]["retry_at"], old["payload"]["error"], quota=True)
            return
        try:
            self.step(claim, old)
        except Exception as exc:
            current = self.owned(claim)
            if current["_etag"] != old["_etag"]:
                raise StoreError("CHANNEL_LEASE_LOST", retryable=True) from None
            code = exc.code if isinstance(exc, StoreError) else error_code(exc)
            quota = isinstance(exc, HTTPException) and code in WAIT_CODES
            delay = retry_seconds(exc, claim["payload"]["attempts"], datetime.now(timezone.utc), WAIT_CODES)
            if isinstance(exc, StoreError) and exc.retry_after:
                delay = max(delay, min(exc.retry_after, 30 * 86400))
            if not quota and old["payload"]["failures"] >= 2:
                delay = max(delay, min(6 * 3600, 900 * 2 ** min(old["payload"]["failures"] - 2, 5)))
            self.wait(
                claim,
                old,
                (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(),
                code,
                quota=quota,
            )

    def wait(self, claim, old, resume, code, *, quota):
        job = self.outbox.current(claim)
        updated, failed = clean(job), clean(old)
        attempts = job["payload"]["attempts"] - int(quota)
        terminal = not quota and (attempts >= 5 or code.endswith("KEY_REQUIRED"))
        if not quota:
            failed["payload"]["failures"] += 1
        updated.update(
            state="failed" if terminal else "pending",
            due_at=min(resume, (datetime.now(timezone.utc) + timedelta(days=6)).isoformat()),
        )
        updated["payload"].update(lease=None, error=code, attempts=attempts)
        failed["payload"].update(error=code, retry_at=resume)
        if terminal:
            updated["finished_at"] = now()
            failed["payload"]["next_poll_at"] = max(
                resume, (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
            )
        self.store.batch(
            "state",
            old["pk"],
            [
                Write("replace", "channel", failed, old["_etag"]),
                Write("replace", job["id"], updated, job["_etag"]),
            ],
        )

    def step(self, claim, old):
        payload, value, pk = claim["payload"], old["payload"], old["pk"]
        ident, stage = value["channel_id"], payload["stage"]
        writes, changed_ids = [], []
        updated = clean(old)
        updated["payload"].update(error="", retry_at=None)
        if stage == "poll":
            args = {"playlistId": value["uploads_id"], "part": "contentDetails", "maxResults": 50}
            if payload.get("cursor"):
                args["pageToken"] = payload["cursor"]
            ids, cursor = uploads_page(
                self.rt.youtube_request("playlistItems", args), payload["cutoff"], payload["seen"]
            )
            continuation = dict(
                cursor=cursor,
                seen=[*payload["seen"], cursor] if cursor else payload["seen"],
                after_video="",
                next_stage="poll" if cursor else "retained",
            )
            write = next_job(
                self.outbox,
                claim,
                stage="videos" if ids else continuation["next_stage"],
                video_ids=ids,
                **continuation,
            )
        elif stage == "retained":
            rows = self.store.page("state", pk, "video", after=payload.get("after_video", ""), limit=50)
            ids = [
                row["payload"]["video_id"]
                for row in rows
                if row["payload"]["updated_at"] < payload["cycle_started"]
            ]
            next_stage = "retained" if len(rows) == 50 else "metadata"
            write = next_job(
                self.outbox,
                claim,
                stage="videos" if ids else next_stage,
                video_ids=ids,
                next_stage=next_stage,
                after_video=rows[-1]["id"] if rows else "",
            )
        elif stage == "videos":
            ids = payload["video_ids"]
            previous = {video_id: self.video(ident, video_id) for video_id in ids}
            videos = video_batch(
                ident,
                ids,
                self.rt.youtube_request("videos", {"id": ",".join(ids), "part": "snippet,status"}),
                previous,
                now(),
            )
            for video in videos:
                row_id = "video:" + video["id"]
                existing = self.store.get("state", pk, row_id)
                # Force immutable references to keep 50 long descriptions below the batch limit.
                ref = self.values.put(pk, video)
                row = document(
                    pk, row_id, "video", video_id=video["id"], value_ref=ref, updated_at=video["updated_at"]
                )
                writes.append(
                    Write(
                        "replace" if existing else "create",
                        row_id,
                        row,
                        existing["_etag"] if existing else None,
                    )
                )
                changed_ids.append(video["id"])
            write = next_job(self.outbox, claim, stage=payload["next_stage"], video_ids=[])
        elif stage == "metadata":
            channels = items(
                self.rt.youtube_request("channels", {"id": ident, "part": "snippet,contentDetails"})
            )
            if len(channels) != 1:
                raise ValueError("CHANNEL_UNAVAILABLE")
            updated["payload"].update(
                channel_details(channels[0], ident),
                updated_at=now(),
                last_success=now(),
                pending_job_id=None,
                failures=0,
                next_poll_at=(datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),
            )
            write = self.outbox.completion(claim)
            changed_ids = None  # Reconcile all known videos when the creator's metadata changes.
        else:
            raise StoreError("CHANNEL_STAGE_INVALID")
        if changed_ids is None or changed_ids:
            fanout = projection_job(pk, 0)
            fanout["payload"].update(operation="channel_changed", channel_id=ident, video_ids=changed_ids)
            writes.append(Write("create", fanout["id"], fanout))
        if self.owned(claim)["_etag"] != old["_etag"]:
            raise StoreError("CHANNEL_LEASE_LOST", retryable=True)
        self.store.batch("state", pk, [Write("replace", "channel", updated, old["_etag"]), write, *writes])

    def schedule(self):
        count = 0
        for row in partition_items(self.store, "indexes", "channels", "channel_route"):
            count += self.enqueue(row["payload"]["channel_id"], scheduled=True)
        return count
