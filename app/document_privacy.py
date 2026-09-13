"""Atomic account revocation followed by retryable, partition-local erasure."""

from app.document_accounts import Outbox, document, now, projection_job
from app.document_store import StoreError, Write, clean, partition_items


class Privacy:
    def __init__(self, runtime):
        self.rt = runtime

    def delete(self, user_id, firebase_project=None):
        rt = self.rt
        if firebase_project and firebase_project != rt.cfg.firebase_project_id:
            raise StoreError("IDENTITY_TARGET_MISMATCH")
        account = rt.accounts.active(user_id)
        feed = rt.store.get("state", account["pk"], "feed")
        tombstone = document(
            account["pk"],
            "account",
            "account",
            user_id=user_id,
            display_name="Deleted account",
            deleted=True,
            config={},
            revision=account["payload"]["revision"] + 1,
            deleted_at=now(),
        )
        revoked = clean(feed)
        revoked["payload"].update(revoked=True, paused=True, token_ciphertext="", generation=None, etag="")
        job = projection_job(account["pk"], tombstone["payload"]["revision"])
        job["payload"].update(operation="account_erasure", firebase_project=firebase_project)
        rt.store.batch(
            "state",
            account["pk"],
            [
                Write("replace", "account", tombstone, account["_etag"]),
                Write("replace", "feed", revoked, feed["_etag"]),
                Write("create", job["id"], job),
            ],
        )
        return {
            "deleted": True,
            "identity_cleanup": "queued" if firebase_project else "not_applicable",
            "external_cache": "请在系统日历中删除旧订阅以清除缓存",
        }

    def erase(self, claim):
        rt, pk = self.rt, claim["pk"]
        account = rt.store.get("state", pk, "account")
        if not account or not account["payload"]["deleted"]:
            raise StoreError("DELETION_NOT_CONFIRMED")
        project = claim["payload"].get("firebase_project")
        if project:
            if project != rt.cfg.firebase_project_id:
                raise StoreError("IDENTITY_TARGET_MISMATCH")
            from firebase_admin import auth
            from app.security import firebase_app

            try:
                auth.delete_user(account["payload"]["user_id"], app=firebase_app())
            except auth.UserNotFoundError:
                pass
            except Exception:
                raise StoreError("IDENTITY_CLEANUP_FAILED", retryable=True) from None
        # Every private document kind is removed, including immutable snapshots and
        # OAuth records. The tombstone remains the authority across retries.
        for row in partition_items(rt.store, "state", pk, None):
            if row["id"] in {"account", "feed", claim["id"]}:
                continue
            Outbox(rt.store).current(claim)
            rt.store.batch("state", pk, [Write("delete", row["id"], etag=row["_etag"])])
        if rt.cfg.env == "local":
            for session in partition_items(rt.store, "state", "local:sessions", "session"):
                if session["payload"]["user_id"] == account["payload"]["user_id"]:
                    rt.store.batch(
                        "state", "local:sessions", [Write("delete", session["id"], etag=session["_etag"])]
                    )
        rt.store.batch("state", pk, [Outbox(rt.store).completion(claim)])
