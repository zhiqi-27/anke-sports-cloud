"""Personal creator commands and retryable links between owner and shared channel partitions."""

from app.document_accounts import Change, Outbox, owner_partition, projection_job
from app.document_channels import channel_partition, next_job
from app.document_store import StoreError, Write
from app.schemas import CreatorFollow
from app.security import digest, problem


def reconcile_job(pk, revision):
    job = projection_job(pk, revision)
    job["payload"]["operation"] = "creator_reconcile"
    return job


class Creators:
    def __init__(self, runtime):
        self.rt, self.store, self.accounts = runtime, runtime.store, runtime.accounts
        self.outbox = Outbox(self.store)

    def validate(self, payload):
        for follow in payload["config"]["creators"]:
            if not self.exists(follow["channel_id"]):
                raise StoreError("DOCUMENT_CONTENT_MIGRATION_REQUIRED")

    def exists(self, ident):
        try:
            return self.rt.channels.get(ident) is not None
        except StoreError as exc:
            if exc.code == "CHANNEL_ID_INVALID":
                return False
            raise

    def views(self, payload):
        result = []
        for follow in payload["config"]["creators"]:
            row = self.rt.channels.get(follow["channel_id"])
            if not row:
                raise StoreError("DOCUMENT_CONTENT_MIGRATION_REQUIRED")
            value = row["payload"]
            job = (
                self.store.get("state", row["pk"], value["pending_job_id"])
                if value["pending_job_id"]
                else None
            )
            error = value["error"]
            if value["pending_job_id"] and not job:
                error = "CHANNEL_JOB_INCOMPLETE"
            elif job and job["state"] == "failed":
                error = job["payload"]["error"] or error
            pending = job and job["state"] in {"pending", "running"}
            result.append(
                {
                    **follow,
                    "name": value["name"],
                    "last_error": error,
                    "sync_status": "syncing"
                    if pending
                    else "error"
                    if error
                    else "current"
                    if value["last_success"]
                    else "pending",
                    "last_synced_at": value["last_success"],
                    "websub_status": "disabled",
                }
            )
        return result

    def followed(self, previous, ident):
        row = next((row for row in previous["config"]["creators"] if row["channel_id"] == ident), None)
        if not row:
            problem("NOT_FOUND", "未关注此创作者", 404)
        return row

    def save(self, user_id, data, *, ident=None, key=None):
        def perform(previous):
            if previous["revision"] != data.expected_revision:
                problem("REVISION_CONFLICT", "配置已更新，请刷新后重试", 409)
            if ident:
                self.followed(previous, ident)
            snapshot = self.rt.catalog.capture()
            sources = {row.id for row in snapshot.sources()}
            if any(source not in sources for source in data.scope_keys):
                problem("SOURCE_NOT_FOUND", "关联范围尚未接入")
            channel_id = ident
            if channel_id is None:
                details = self.rt.resolve_creator(data.url)
                self.rt.channels.ensure(details)
                channel_id = details["channel_id"]
            follow = CreatorFollow(
                channel_id=channel_id,
                scope_keys=list(dict.fromkeys(data.scope_keys)),
                preview=data.preview,
                recap=data.recap,
                enabled=data.enabled if ident else True,
            ).model_dump()
            creators = [row for row in previous["config"]["creators"] if row["channel_id"] != channel_id] + [
                follow
            ]
            updated = {
                **previous,
                "revision": previous["revision"] + 1,
                "config": {**previous["config"], "creators": creators},
            }
            snapshot.assert_current()
            return Change(updated, self.rt.user_view(updated, pending=True))

        return self.accounts.command(
            user_id,
            "update_creator" if ident else "add_creator",
            {"channel_id": ident, **data.model_dump()},
            perform,
            key=key,
        )

    def impact(self, previous, ident):
        self.followed(previous, ident)
        rows = self.rt.content.rows(previous["user_id"])
        snapshot = self.rt.catalog.capture()
        automatic, retained = 0, 0
        for event_id in {row.event_id for row in rows if row.channel_id == ident}:
            event = snapshot.event(event_id)
            if not event:
                continue
            for link in self.rt.content.selected(event, previous, rows=rows):
                if not any(row.id == link["id"] and row.channel_id == ident for row in rows):
                    continue
                if link["origin"] == "automatic" and not link["pinned"]:
                    automatic += 1
                else:
                    retained += 1
        snapshot.assert_current()
        return {"automatic_removed": automatic, "manual_retained": retained, "revision": previous["revision"]}

    def remove(self, user_id, ident, revision, confirmed):
        if not confirmed:
            problem("CONFIRM_REQUIRED", "请查看删除影响并确认")

        def prepare(previous):
            self.followed(previous, ident)
            return {
                **previous["config"],
                "creators": [row for row in previous["config"]["creators"] if row["channel_id"] != ident],
            }

        return self.accounts.save_config(
            user_id,
            None,
            revision,
            prepare=prepare,
            response=lambda updated: self.rt.user_view(updated, pending=True),
        )

    def refresh(self, user_id, ident):
        def perform(previous):
            follow = self.followed(previous, ident)
            if not follow["enabled"]:
                problem("CREATOR_PAUSED", "请先恢复此创作者的更新", 409)
            job = reconcile_job(owner_partition(user_id), previous["revision"])
            job["payload"]["force_channel"] = ident
            return Change(previous, {"queued": True}, [Write("create", job["id"], job)])

        return self.accounts.command(user_id, "refresh_creator", {"channel_id": ident}, perform)

    def reconcile(self, claim):
        raw = self.store.get("state", claim["pk"], "account")
        if not raw or raw["payload"]["deleted"]:
            raise StoreError("ACCOUNT_DELETED")
        account = self.accounts.active(raw["payload"]["user_id"])
        config = account["payload"]["config"]
        # A new config revision starts from the beginning, never skips an inserted channel.
        after = (
            claim["payload"].get("after_channel", "")
            if (claim["payload"].get("for_revision") == account["payload"]["revision"])
            else ""
        )
        channels = sorted(row["channel_id"] for row in config["creators"] if row["channel_id"] > after)
        writes = []
        for ident in channels[:10]:
            self.rt.channels.register(ident, account["pk"])
            self.rt.channels.enqueue(ident, scheduled=ident != claim["payload"].get("force_channel"))
            job = projection_job(account["pk"], account["payload"]["revision"])
            job["payload"].update(operation="match_channel", channel_id=ident)
            writes.append(Write("create", job["id"], job))
        completion = (
            next_job(
                self.outbox, claim, after_channel=channels[9], for_revision=account["payload"]["revision"]
            )
            if len(channels) > 10
            else self.outbox.completion(claim)
        )
        self.store.batch("state", account["pk"], [self.accounts.guard(account), completion, *writes])

    def fanout(self, claim):
        ident = claim["payload"]["channel_id"]
        if claim["pk"] != channel_partition(ident):
            raise StoreError("CHANNEL_JOB_INVALID")
        routes = self.store.page(
            "indexes", claim["pk"], "channel_owner", after=claim["payload"].get("after_owner", ""), limit=50
        )
        for route in routes:
            pk = route["payload"]["owner_pk"]
            raw = self.store.get("state", pk, "account")
            if not raw or raw["payload"]["deleted"]:
                continue
            account = self.accounts.active(raw["payload"]["user_id"])
            job = projection_job(pk, account["payload"]["revision"])
            job["id"] = "job:" + digest(claim["pk"] + ":" + claim["id"] + ":" + pk)[:32]
            job["payload"].update(
                operation="match_channel", channel_id=ident, video_ids=claim["payload"].get("video_ids")
            )
            if self.store.get("state", pk, job["id"]):
                continue
            self.store.batch("state", pk, [self.accounts.guard(account), Write("create", job["id"], job)])
        completion = (
            next_job(self.outbox, claim, after_owner=routes[-1]["id"])
            if len(routes) == 50
            else (self.outbox.completion(claim))
        )
        self.store.batch("state", claim["pk"], [completion])
