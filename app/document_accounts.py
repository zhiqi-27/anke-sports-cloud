"""Authoritative account commands and leased outbox, each atomic in one partition."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
import secrets
import time
from uuid import uuid4

from app.document_store import Conflict, StoreError, Write, clean, encode
from app.schemas import Config
from app.security import digest, problem


def now():
    return datetime.now(timezone.utc).isoformat()


def owner_partition(user_id):
    if not isinstance(user_id, str) or not 0 < len(user_id) <= 128:
        raise StoreError("ACCOUNT_SUBJECT_INVALID")
    return "user:" + digest(user_id)


def document(pk, ident, kind, **payload):
    return {"pk": pk, "id": ident, "kind": kind, "schema": 1, "payload": payload}


def projection_job(pk, revision):
    job = document(
        pk,
        "job:" + uuid4().hex,
        "outbox",
        operation="projection",
        revision=revision,
        attempts=0,
        lease=None,
        error="",
    )
    return {**job, "state": "pending", "due_at": now()}


class Accounts:
    def __init__(self, store, cipher):
        self.store, self.cipher = store, cipher

    def active(self, user_id):
        row = self.store.get("state", owner_partition(user_id), "account")
        if not row or row["payload"]["deleted"]:
            problem("ACCOUNT_DELETED", "账号已删除", 403)
        return row

    def ensure(self, user_id):
        pk = owner_partition(user_id)
        current = self.store.get("state", pk, "account")
        if current:
            return self.active(user_id)
        user = document(
            pk,
            "account",
            "account",
            user_id=user_id,
            display_name="Anke Sports 用户",
            revision=0,
            deleted=False,
            config=Config().model_dump(),
        )
        token = secrets.token_urlsafe(32)
        feed = document(
            pk,
            "feed",
            "feed",
            feed_id=uuid4().hex,
            token_hash=digest(token),
            token_ciphertext=self.cipher.encrypt(token.encode()).decode(),
            paused=False,
            revoked=False,
            revision=0,
            updated_at=now(),
            generation=None,
            etag="",
            event_count=0,
        )
        job = projection_job(pk, 0)
        try:
            self.store.batch("state", pk, [Write("create", d["id"], d) for d in [user, feed, job]])
        except Conflict:
            # Another login may have won. Never overwrite a tombstone or its Feed identity.
            return self.active(user_id)
        return self.active(user_id)

    def save_config(self, user_id, config, revision, *, key=None, operation="set_config", payload=None):
        current = self.active(user_id)  # Always before a cached command response.
        pk, previous = current["pk"], current["payload"]
        receipt_id, receipt, fingerprint = None, None, None
        if key is not None:
            if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", key):
                problem("INVALID_IDEMPOTENCY_KEY", "幂等键需要 8 至 128 个字母、数字或 ._:-")
            receipt_id = "receipt:" + digest(key)
            fingerprint = digest(
                encode(payload if payload is not None else {"config": config, "revision": revision})
            )
            receipt = self.store.get("state", pk, receipt_id)
            if receipt and receipt["payload"]["expires_at"] > time.time():
                value = receipt["payload"]
                if value["operation"] != operation or value["fingerprint"] != fingerprint:
                    problem("IDEMPOTENCY_CONFLICT", "此幂等键已用于不同操作", 409)
                return value["result"]
        if previous["revision"] != revision:
            problem("REVISION_CONFLICT", "配置已在其他页面更新，请刷新后重试", 409)
        updated = clean(current)
        updated["payload"] = {
            **previous,
            "config": Config.model_validate(config).model_dump(),
            "revision": revision + 1,
        }
        result = deepcopy(updated["payload"])
        job = projection_job(pk, revision + 1)
        writes = [Write("replace", "account", updated, current["_etag"]), Write("create", job["id"], job)]
        if receipt_id:
            saved = document(
                pk,
                receipt_id,
                "receipt",
                operation=operation,
                fingerprint=fingerprint,
                result=result,
                expires_at=int(time.time()) + 86400,
            )
            writes.append(
                Write(
                    "replace" if receipt else "create",
                    receipt_id,
                    saved,
                    receipt["_etag"] if receipt else None,
                )
            )
        try:
            self.store.batch("state", pk, writes)
        except Conflict:
            # A response may have been lost after another identical request committed.
            # Re-enter receipt lookup, but never silently rebase a configuration command.
            fresh = self.active(user_id)
            if key and fresh["payload"]["revision"] != revision:
                prior = self.store.get("state", pk, receipt_id)
                if prior and prior["payload"]["expires_at"] > time.time():
                    value = prior["payload"]
                    if value["operation"] == operation and value["fingerprint"] == fingerprint:
                        return value["result"]
            problem("REVISION_CONFLICT", "配置已在其他页面更新，请刷新后重试", 409)
        return result

    def address(self, user_id):
        self.active(user_id)
        feed = self.store.get("state", owner_partition(user_id), "feed")
        data = feed["payload"]
        if data["revoked"]:
            problem("FEED_REVOKED", "订阅地址已撤销", 404)
        # A lookup is only a route. The authoritative account/Feed is rechecked on every read.
        route = document(
            "token:" + data["token_hash"][:2], data["token_hash"], "feed_route", owner_pk=feed["pk"]
        )
        try:
            self.store.batch("indexes", route["pk"], [Write("create", route["id"], route)])
        except Conflict:
            existing = self.store.get("indexes", route["pk"], route["id"])
            if not existing or existing["payload"] != route["payload"]:
                raise StoreError("FEED_ROUTE_CONFLICT") from None
        return self.cipher.decrypt(data["token_ciphertext"].encode()).decode()

    def rotate(self, user_id):
        account = self.active(user_id)
        old = self.store.get("state", account["pk"], "feed")
        token = secrets.token_urlsafe(32)
        new = clean(old)
        new["payload"] = {
            **old["payload"],
            "token_hash": digest(token),
            "token_ciphertext": self.cipher.encrypt(token.encode()).decode(),
            "revoked": False,
        }
        self.store.batch(
            "state",
            account["pk"],
            [
                Write("replace", "account", clean(account), account["_etag"]),
                Write("replace", "feed", new, old["_etag"]),
            ],
        )
        return self.address(user_id)

    def feed_for_token(self, token):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            problem("FEED_NOT_FOUND", "未找到订阅", 404)
        hashed = digest(token)
        route = self.store.get("indexes", "token:" + hashed[:2], hashed)
        if not route:
            problem("FEED_NOT_FOUND", "未找到订阅", 404)
        pk = route["payload"]["owner_pk"]
        account, feed = self.store.get("state", pk, "account"), self.store.get("state", pk, "feed")
        if (
            not account
            or account["payload"]["deleted"]
            or not feed
            or feed["payload"]["revoked"]
            or (feed["payload"]["token_hash"] != hashed)
        ):
            problem("FEED_NOT_FOUND", "未找到订阅", 404)
        return feed


class Outbox:
    def __init__(self, store):
        self.store = store

    def claim(self, pk, ident, *, instant=None):
        instant = instant or datetime.now(timezone.utc)
        job = self.store.get("state", pk, ident)
        if not job or job["kind"] != "outbox" or job["state"] not in {"pending", "running"}:
            return None
        if job["due_at"] > instant.isoformat():
            return None
        claimed = clean(job)
        attempts = job["payload"]["attempts"]
        claimed.update(
            state="failed" if attempts >= 5 else "running",
            due_at=(instant + timedelta(minutes=5)).isoformat(),
        )
        claimed["payload"] = {
            **job["payload"],
            "attempts": attempts + (attempts < 5),
            "lease": uuid4().hex,
            "error": "ATTEMPTS_EXHAUSTED" if attempts >= 5 else "",
        }
        try:
            self.store.batch("state", pk, [Write("replace", ident, claimed, job["_etag"])])
        except Conflict:
            return None
        result = self.store.get("state", pk, ident)
        return (
            result
            if result
            and result["state"] == "running"
            and (result["payload"]["lease"] == claimed["payload"]["lease"])
            else None
        )

    def current(self, claim, *, instant=None):
        job = self.store.get("state", claim["pk"], claim["id"])
        instant = instant or datetime.now(timezone.utc)
        if (
            not job
            or job["state"] != "running"
            or job["payload"]["lease"] != claim["payload"]["lease"]
            or (job["due_at"] <= instant.isoformat())
        ):
            raise StoreError("JOB_LEASE_LOST", retryable=True)
        return job

    def completion(self, claim):
        job = self.current(claim)
        completed = clean(job)
        completed.update(state="done", finished_at=now())
        return Write("replace", job["id"], completed, job["_etag"])
