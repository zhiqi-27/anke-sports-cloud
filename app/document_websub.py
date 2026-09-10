"""Durable Hub intent and notification inbox in the shared channel partition."""

from datetime import datetime, timedelta, timezone
import hmac
import re
import secrets

import httpx

from app.document_accounts import Outbox, document, now, projection_job
from app.document_channels import channel_partition
from app.document_store import Conflict, StoreError, Write, clean, partition_items
from app.job_rules import error_code, retry_seconds
from app.security import digest, problem
from app.websub_rules import HUB, callback_url, notification_entries, topic, verification_digest


def future(**kwargs):
    return (datetime.now(timezone.utc) + timedelta(**kwargs)).isoformat()


class WebSub:
    def __init__(self, runtime):
        self.rt, self.store = runtime, runtime.store
        self.outbox = Outbox(self.store)

    def get(self, ident):
        return self.store.get("state", channel_partition(ident), "websub")

    def status(self, ident):
        row = self.get(ident)
        if not self.rt.cfg.youtube_websub_enabled:
            return "disabled"
        if not row:
            return "pending"
        value = row["payload"]
        if value["state"] == "verified" and value["lease_expires_at"] <= now():
            return "expired"
        return value["state"]

    def ensure(self, ident):
        row = self.get(ident)
        if row:
            return row
        callback = secrets.token_urlsafe(32)
        pk = channel_partition(ident)
        row = document(
            pk,
            "websub",
            "websub",
            channel_id=ident,
            callback_id=callback,
            secret_ciphertext=self.rt.cfg.cipher().encrypt(secrets.token_urlsafe(32).encode()).decode(),
            state="disabled",
            lease_expires_at=None,
            renew_at=None,
            pending_until=None,
            retry_at=None,
            pending_job_id=None,
            verification_digest="",
            request_id=None,
            last_notification_at=None,
            error="",
        )
        route = document("youtube:callbacks", digest(callback), "youtube_callback", channel_id=ident)
        # An orphan route has no authority; callbacks require the matching channel record.
        self.store.batch("indexes", route["pk"], [Write("create", route["id"], route)])
        try:
            self.store.batch("state", pk, [Write("create", "websub", row)])
        except Conflict:
            pass
        return self.get(ident)

    def desired(self, ident):
        return self.rt.cfg.youtube_websub_enabled and self.rt.channels.interested(ident)

    def due(self, value, active):
        if active:
            return (
                not value["renew_at"]
                or value["renew_at"] <= now()
                or value["state"] not in {"verified", "renewing"}
            )
        return value["state"] not in {"disabled", "unsubscribed", "expired"}

    def schedule_channel(self, ident):
        old = self.get(ident)
        active = self.desired(ident)
        if not old:
            if not active:
                return False
            old = self.ensure(ident)
        value = old["payload"]
        if value["pending_job_id"]:
            job = self.store.get("state", old["pk"], value["pending_job_id"])
            if not job:
                raise StoreError("WEBSUB_JOB_INCOMPLETE", retryable=True)
            if job["state"] in {"pending", "running"}:
                return False
        if value["retry_at"] and value["retry_at"] > now() or not self.due(value, active):
            return False
        job = projection_job(old["pk"], 0)
        job["payload"].update(operation="youtube_subscribe", channel_id=ident)
        updated = clean(old)
        updated["payload"]["pending_job_id"] = job["id"]
        self.store.batch(
            "state",
            old["pk"],
            [Write("replace", "websub", updated, old["_etag"]), Write("create", job["id"], job)],
        )
        return True

    def owned(self, claim):
        self.outbox.current(claim)
        old = self.get(claim["payload"]["channel_id"])
        if not old or old["pk"] != claim["pk"] or old["payload"]["pending_job_id"] != claim["id"]:
            raise StoreError("WEBSUB_LEASE_LOST", retryable=True)
        return old

    def settle(self, claim, old, *, resume=None, code="", passive=False):
        current = self.outbox.current(claim)
        updated, job = clean(old), clean(current)
        terminal = resume is not None and not passive and current["payload"]["attempts"] >= 5
        if terminal and not code:
            code = "HUB_VERIFICATION_TIMEOUT"
        job.update(state="failed" if terminal else "pending" if resume else "done")
        job["payload"].update(lease=None, error=code)
        if passive:
            job["payload"]["attempts"] = max(0, job["payload"]["attempts"] - 1)
        if resume:
            job["due_at"] = min(resume, future(days=6))
            if terminal:
                resume = max(resume, future(hours=6))
            updated["payload"]["retry_at"] = resume
        else:
            updated["payload"].update(pending_job_id=None, retry_at=None)
        if code:
            updated["payload"]["error"] = code
        if job["state"] in {"done", "failed"}:
            job["finished_at"] = now()
        self.store.batch(
            "state",
            old["pk"],
            [
                Write("replace", "websub", updated, old["_etag"]),
                Write("replace", job["id"], job, current["_etag"]),
            ],
        )

    def process(self, claim):
        old = self.owned(claim)
        value, ident = old["payload"], old["payload"]["channel_id"]
        active = self.desired(ident)
        if not self.due(value, active):
            self.settle(claim, old)
            return
        waiting = value["retry_at"]
        if value["pending_until"] and value["pending_until"] > now():
            expected = value["state"] != "unsubscribing"
            if expected == active:
                waiting = max(waiting or "", value["pending_until"])
        if waiting and waiting > now():
            self.settle(claim, old, resume=waiting, passive=True)
            return
        try:
            callback = callback_url(self.rt.cfg.public_url, value["callback_id"])
        except ValueError as exc:
            self.settle(claim, old, resume=future(hours=6), code=error_code(exc))
            return
        updated = clean(old)
        intent = secrets.token_hex(16)
        updated["payload"].update(
            state=(
                "renewing" if value["lease_expires_at"] and value["lease_expires_at"] > now() else "pending"
            )
            if active
            else "unsubscribing",
            pending_until=future(minutes=15),
            retry_at=None,
            request_id=intent,
            verification_digest="",
            error="",
        )
        job = self.outbox.current(claim)
        self.store.batch(
            "state",
            old["pk"],
            [
                Write("replace", "websub", updated, old["_etag"]),
                Write("replace", job["id"], clean(job), job["_etag"]),
            ],
        )
        self.owned(claim)  # Never send from an expired task after preparing an intent.
        failure = None
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                response = client.post(
                    HUB,
                    data={
                        "hub.callback": callback,
                        "hub.topic": topic(ident),
                        "hub.mode": "subscribe" if active else "unsubscribe",
                        "hub.verify": "async",
                        "hub.lease_seconds": "432000",
                        "hub.secret": self.rt.cfg.cipher()
                        .decrypt(value["secret_ciphertext"].encode())
                        .decode(),
                    },
                )
            if response.status_code not in {202, 204}:
                response.raise_for_status()
                raise ValueError("HUB_REQUEST_REJECTED")
        except (httpx.HTTPError, ValueError) as exc:
            failure = exc
        current = self.owned(claim)
        if current["payload"]["request_id"] != intent:
            raise StoreError("WEBSUB_INTENT_CHANGED", retryable=True)
        # Verification may arrive before the outbound HTTP finishes; never overwrite it.
        if current["payload"]["state"] == "denied":
            self.settle(claim, current, resume=current["payload"]["retry_at"], code="HUB_DENIED")
        elif current["payload"]["state"] in {"verified", "unsubscribed"}:
            self.settle(claim, current)
        elif failure:
            delay = retry_seconds(failure, claim["payload"]["attempts"], datetime.now(timezone.utc))
            self.settle(
                claim,
                current,
                resume=max(current["payload"]["pending_until"], future(seconds=delay)),
                code=error_code(failure),
            )
        else:
            self.settle(claim, current, resume=current["payload"]["pending_until"])

    def callback(self, callback_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,100}", callback_id):
            return None
        route = self.store.get("indexes", "youtube:callbacks", digest(callback_id))
        row = self.get(route["payload"]["channel_id"]) if route else None
        return row if row and hmac.compare_digest(row["payload"]["callback_id"], callback_id) else None

    def verify(self, callback_id, params):
        old = self.callback(callback_id)
        if not old or params.get("hub.topic") != topic(old["payload"]["channel_id"]):
            problem("NOT_FOUND", "未找到订阅", 404)
        value = old["payload"]
        active = self.desired(value["channel_id"])
        mode, challenge = params.get("hub.mode"), params.get("hub.challenge", "")
        fingerprint = verification_digest(params)
        duplicate = value["verification_digest"] == fingerprint and (
            active
            and mode == "subscribe"
            and value["state"] == "verified"
            and value["lease_expires_at"] > now()
            or not active
            and mode == "unsubscribe"
            and value["state"] == "unsubscribed"
        )
        if challenge and len(challenge) <= 2048 and duplicate:
            return challenge
        pending = (
            value["pending_until"]
            and value["pending_until"] >= now()
            and value["state"] in {"pending", "renewing", "unsubscribing"}
            and (value["state"] != "unsubscribing") == active
        )
        denied_active_lease = (
            mode == "denied"
            and active
            and value["state"] in {"verified", "renewing"}
            and value["lease_expires_at"]
            and value["lease_expires_at"] > now()
        )
        if not pending and not denied_active_lease:
            problem("NOT_FOUND", "没有待确认的订阅", 404)
        updated = clean(old)
        if mode == "denied":
            updated["payload"].update(
                state="denied",
                error="HUB_DENIED",
                pending_until=None,
                lease_expires_at=None,
                renew_at=None,
                retry_at=future(hours=6),
            )
        else:
            if mode != ("subscribe" if active else "unsubscribe"):
                problem("NOT_FOUND", "未请求此订阅操作", 404)
            if not challenge or len(challenge) > 2048:
                problem("INVALID_CHALLENGE", "验证内容无效")
            lease = None
            if active:
                try:
                    lease = int(params.get("hub.lease_seconds", ""))
                except (TypeError, ValueError):
                    problem("INVALID_LEASE", "租约无效")
                if not 60 <= lease <= 90 * 86400:
                    problem("INVALID_LEASE", "租约无效")
            updated["payload"].update(
                state="verified" if active else "unsubscribed",
                error="",
                pending_until=None,
                retry_at=None,
                verification_digest=fingerprint,
                lease_expires_at=future(seconds=lease) if active else None,
                renew_at=future(seconds=lease * 0.8) if active else None,
            )
        self.store.batch("state", old["pk"], [Write("replace", "websub", updated, old["_etag"])])
        return "" if mode == "denied" else challenge

    def notification(self, callback_id, body, signature):
        old = self.callback(callback_id)
        if not old or not self.rt.cfg.youtube_websub_enabled:
            return False
        value, pk = old["payload"], old["pk"]
        if (
            value["state"] not in {"verified", "renewing"}
            or not value["lease_expires_at"]
            or (value["lease_expires_at"] <= now() or not self.rt.channels.interested(value["channel_id"]))
        ):
            return False
        entries = notification_entries(
            value["channel_id"],
            body,
            signature,
            self.rt.cfg.cipher().decrypt(value["secret_ciphertext"].encode()),
        )
        if not entries:
            return False
        writes = []
        for entry in entries:
            ident = "notice:" + entry["key"]
            if self.store.get("state", pk, ident):
                continue
            notice = document(
                pk, ident, "youtube_notice_pending", video_id=entry["video_id"], received_at=now()
            )
            writes.append(Write("create", ident, notice))
        if not writes:
            return True
        job = projection_job(pk, 0)
        job["payload"].update(operation="channel_notice", channel_id=value["channel_id"])
        updated = clean(old)
        updated["payload"]["last_notification_at"] = now()
        self.store.batch(
            "state",
            pk,
            [Write("replace", "websub", updated, old["_etag"]), Write("create", job["id"], job), *writes],
        )
        return True

    def prune(self, ident):
        # Pending work is retained. Only processed dedupe hints expire after seven days.
        pk, cutoff = channel_partition(ident), future(days=-7)
        writes = []
        for row in partition_items(self.store, "state", pk, "youtube_notice_done"):
            if row["payload"]["received_at"] < cutoff:
                writes.append(Write("delete", row["id"], etag=row["_etag"]))
                if len(writes) == 100:
                    break
        if writes:
            self.store.batch("state", pk, writes)
