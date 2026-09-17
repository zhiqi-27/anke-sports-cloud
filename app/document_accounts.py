"""Authoritative account commands and leased outbox, each atomic in one partition."""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
import secrets
import time
from uuid import uuid4

from pydantic import ValidationError

from app.document_store import Conflict, StoreError, Write, clean, encode
from app.schemas import Config
from app.document_values import Values
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


@dataclass
class Change:
    payload: dict
    result: dict
    writes: list[Write] = field(default_factory=list)


class Accounts:
    def __init__(self, store, cipher):
        self.store, self.cipher = store, cipher
        self.values = Values(store)

    def active(self, user_id):
        row = self.store.get("state", owner_partition(user_id), "account")
        if not row or row["payload"]["deleted"]:
            problem("ACCOUNT_DELETED", "账号已删除", 403)
        if "config_ref" in row["payload"]:
            raw = clean(row)
            payload = {key: value for key, value in row["payload"].items() if key != "config_ref"}
            payload["config"] = self.values.unpack(row["pk"], "config", row["payload"])
            row = {**row, "payload": payload, "_raw": raw}
        return row

    def guard(self, row):
        return Write("replace", "account", row.get("_raw", clean(row)), row["_etag"])

    def ensure(self, user_id):
        pk = owner_partition(user_id)
        current = self.store.get("state", pk, "account")
        if current:
            return self.active(user_id)
        # Register before creation so a crash cannot hide an owner from catalog
        # fanout. This index is never identity authority; consumers recheck state.
        route = document("owners", pk, "owner_route", owner_pk=pk)
        try:
            self.store.batch("indexes", "owners", [Write("create", pk, route)])
        except Conflict:
            pass
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

    def command(self, user_id, operation, payload, perform, *, key=None):
        current = self.active(user_id)  # Identity/deletion precedes receipt replay.
        pk, previous = current["pk"], current["payload"]
        receipt_id, receipt = None, None
        fingerprint = digest(encode(payload))

        def cached(row):
            if not row or row["payload"]["expires_at"] <= time.time():
                return None
            value = row["payload"]
            if value["operation"] != operation or value["fingerprint"] != fingerprint:
                problem("IDEMPOTENCY_CONFLICT", "此幂等键已用于不同操作", 409)
            return self.values.unpack(pk, "result", value)

        if key is not None:
            if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", key):
                problem("INVALID_IDEMPOTENCY_KEY", "幂等键需要 8 至 128 个字母、数字或 ._:-")
            receipt_id = "receipt:" + digest(key)
            receipt = self.store.get("state", pk, receipt_id)
            result = cached(receipt)
            if result is not None:
                return result
        change = perform(deepcopy(previous))
        updated = deepcopy(change.payload)
        if (
            updated["user_id"] != user_id
            or updated["deleted"]
            or updated["revision"] not in {previous["revision"], previous["revision"] + 1}
        ):
            raise StoreError("ACCOUNT_MUTATION_INVALID")
        try:
            config = Config.model_validate(updated.pop("config")).model_dump()
        except ValidationError:
            problem("CONFIG_LIMIT_EXCEEDED", "配置超过支持的数量上限，请减少内容后重试")
        updated.update(self.values.pack(pk, "config", config))
        changed = change.payload["revision"] != previous["revision"]
        writes = []
        if changed:
            account = document(pk, "account", "account", **updated)
            job = projection_job(pk, change.payload["revision"])
            writes.extend(
                [
                    Write("replace", "account", account, current["_etag"]),
                    Write("create", job["id"], job),
                ]
            )
        writes.extend(change.writes)
        if receipt_id:
            saved = document(
                pk,
                receipt_id,
                "receipt",
                operation=operation,
                fingerprint=fingerprint,
                expires_at=int(time.time()) + 86400,
                **self.values.pack(pk, "result", change.result),
            )
            writes.append(
                Write(
                    "replace" if receipt else "create",
                    receipt_id,
                    saved,
                    receipt["_etag"] if receipt else None,
                )
            )
        if not writes:
            return change.result
        try:
            self.store.batch("state", pk, writes)
        except Conflict:
            self.active(user_id)
            if receipt_id:
                result = cached(self.store.get("state", pk, receipt_id))
                if result is not None:
                    return result
            problem("REVISION_CONFLICT", "配置已在其他页面更新，请刷新后重试", 409)
        return change.result

    def save_config(
        self,
        user_id,
        config,
        revision,
        *,
        key=None,
        operation="set_config",
        payload=None,
        prepare=None,
        response=None,
    ):
        def perform(previous):
            if previous["revision"] != revision:
                problem("REVISION_CONFLICT", "配置已在其他页面更新，请刷新后重试", 409)
            prepared = prepare(deepcopy(previous)) if prepare else config
            try:
                prepared = Config.model_validate(prepared).model_dump()
            except ValidationError:
                problem("CONFIG_LIMIT_EXCEEDED", "配置超过支持的数量上限，请减少内容后重试")
            updated = {
                **previous,
                "config": prepared,
                "revision": revision + 1,
            }
            result = response(deepcopy(updated)) if response else deepcopy(updated)
            return Change(updated, result)

        return self.command(
            user_id,
            operation,
            payload if payload is not None else {"config": config, "revision": revision},
            perform,
            key=key,
        )

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
                self.guard(account),
                Write("replace", "feed", new, old["_etag"]),
            ],
        )
        return self.address(user_id)

    def pause(self, user_id, paused):
        account = self.active(user_id)
        old = self.store.get("state", account["pk"], "feed")
        new = clean(old)
        new["payload"] = {**old["payload"], "paused": paused}
        writes = [
            self.guard(account),
            Write("replace", "feed", new, old["_etag"]),
        ]
        if not paused:
            job = projection_job(account["pk"], account["payload"]["revision"])
            writes.append(Write("create", job["id"], job))
        self.store.batch("state", account["pk"], writes)

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

    def fail(self, claim, error):
        job = self.current(claim)
        retryable = isinstance(error, Conflict) or (isinstance(error, StoreError) and error.retryable)
        delay = min(3600, max(2 ** job["payload"]["attempts"], getattr(error, "retry_after", None) or 0))
        updated = clean(job)
        updated.update(
            state="pending" if retryable and job["payload"]["attempts"] < 5 else "failed",
            due_at=(datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(),
        )
        updated["payload"] = {
            **job["payload"],
            "lease": None,
            "error": error.code if isinstance(error, StoreError) else "JOB_EXECUTION_FAILED",
        }
        if updated["state"] == "failed":
            updated["finished_at"] = now()
        self.store.batch("state", job["pk"], [Write("replace", job["id"], updated, job["_etag"])])
