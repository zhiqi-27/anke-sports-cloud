"""Bounded personal matching and review commands using the shared deterministic rules."""

from types import SimpleNamespace

from app.calendar_rules import event_keys, included
from app.config_rules import link_override
from app.document_accounts import Change, Outbox, document, now, owner_partition, projection_job
from app.document_channels import channel_partition, next_job
from app.document_store import StoreError, Write, partition_items
from app.matching import evaluate
from app.security import digest, problem


def record(pk, ident, kind, value):
    row = document(pk, ident, kind)
    row["payload"] = value
    return row


class Matches:
    def __init__(self, runtime):
        self.rt, self.store, self.accounts = runtime, runtime.store, runtime.accounts
        self.outbox = Outbox(self.store)

    def account(self, pk):
        row = self.store.get("state", pk, "account")
        if not row or row["payload"]["deleted"]:
            raise StoreError("ACCOUNT_DELETED")
        return self.accounts.active(row["payload"]["user_id"])

    def channel(self, claim):
        account = self.account(claim["pk"])
        ident = claim["payload"]["channel_id"]
        after = claim["payload"].get("after_video", "")
        ids = claim["payload"].get("video_ids")
        if ids is None:
            rows = self.store.page(
                "state", channel_partition(ident), "video", after="video:" + after if after else "", limit=25
            )
            ids = [row["payload"]["video_id"] for row in rows]
        else:
            ids = sorted(set(video_id for video_id in ids if video_id > after))[:25]
        writes = []
        for video_id in ids:
            job = projection_job(account["pk"], account["payload"]["revision"])
            job["payload"].update(operation="match_video", channel_id=ident, video_id=video_id)
            writes.append(Write("create", job["id"], job))
        completion = (
            next_job(self.outbox, claim, after_video=ids[-1])
            if len(ids) == 25
            else (self.outbox.completion(claim))
        )
        self.store.batch("state", account["pk"], [self.accounts.guard(account), completion, *writes])

    def link_value(self, user_id, event_id, video, creator, kind, *, origin="automatic", old=None):
        url = "https://www.youtube.com/watch?v=" + video["id"]
        value = (
            dict(old)
            if old
            else {
                "id": digest(user_id + ":" + event_id + ":" + url)[:32],
                "owner_id": user_id,
                "event_id": event_id,
                "url": url,
                "url_hash": digest(url),
                "title": video["title"],
                "kind": kind,
                "platform": "YouTube",
                "access": "unknown",
                "regions": [],
                "created_at": now(),
            }
        )
        value.update(
            origin=origin,
            kind=kind,
            available=video["available"],
            channel_id=video["channel_id"],
            creator=creator["name"],
        )
        return value

    def video(self, claim):
        account = self.account(claim["pk"])
        user, pk = account["payload"], account["pk"]
        channel_id, video_id = claim["payload"]["channel_id"], claim["payload"]["video_id"]
        video = self.rt.channels.video(channel_id, video_id)
        if video is None:
            self.store.batch("state", pk, [self.accounts.guard(account), self.outbox.completion(claim)])
            return
        creator = self.rt.channels.get(channel_id)
        if not creator:
            raise StoreError("CHANNEL_NOT_FOUND")
        follow = next((row for row in user["config"]["creators"] if row["channel_id"] == channel_id), None)
        snapshot = self.rt.catalog.capture()
        events = {row.id: row for row in snapshot.events()}
        feed = self.store.get("state", pk, "feed")["payload"]
        previous = self.rt.publisher.generations.load(pk, feed["generation"])
        retained = {row["event_id"] for row in previous["projections"] if not row["removed"]}
        candidates = [
            event
            for event in events.values()
            if follow
            and (included(event, user["config"]) or event.id in retained)
            and (not follow["scope_keys"] or event_keys(event).intersection(follow["scope_keys"]))
        ]
        outputs = (
            {row["event_id"]: row for row in evaluate(SimpleNamespace(**video), candidates)}
            if (follow and follow["enabled"] and video["available"])
            else {}
        )
        matches = {
            row["payload"]["event_id"]: row
            for row in partition_items(self.store, "state", pk, "video_match")
            if row["payload"]["video_id"] == video_id
        }
        url = "https://www.youtube.com/watch?v=" + video_id
        links = {
            row["payload"]["event_id"]: row
            for row in partition_items(self.store, "state", pk, "link")
            if row["payload"]["url"] == url
        }
        overrides = {
            row["event_key"]: row["state"] for row in user["config"]["link_overrides"] if row["url"] == url
        }
        version = digest(str([(root["pk"], root["payload"]["generation"]) for root in snapshot.roots]))
        identity = [user["revision"], video["updated_at"], version]
        after = (
            claim["payload"].get("after_event", "")
            if claim["payload"].get("input_version") == identity
            else ""
        )
        event_ids = sorted(eid for eid in set(outputs) | set(matches) | set(links) if eid > after)
        writes = []
        for eid in event_ids[:20]:
            event, match, link = events.get(eid), matches.get(eid), links.get(eid)
            state = overrides.get(event.source_key) if event else None
            output = outputs.get(eid)
            match_value = dict(match["payload"]) if match else None
            manual = match_value and match_value["decision"] in {"confirmed", "ignored"}
            if output and not manual:
                match_value = {
                    **(match_value or {}),
                    **output,
                    "id": match["payload"]["id"]
                    if match
                    else digest(user["user_id"] + ":" + video_id + ":" + eid)[:32],
                    "owner_id": user["user_id"],
                    "video_id": video_id,
                    "channel_id": channel_id,
                    "source_updated_at": video["updated_at"],
                    "event_updated_at": event.updated_at,
                }
                if state == "block":
                    match_value["decision"] = "ignored"
                if output["kind"] != "unknown" and not follow.get(output["kind"], False):
                    match_value["decision"] = "reject"
            elif match_value and not manual and (not follow or follow["enabled"] or not video["available"]):
                match_value["decision"] = "retired"
                match_value["source_updated_at"] = video["updated_at"]
            if match_value and (not match or match_value != match["payload"]):
                match_value["updated_at"] = now()
                row_id = "match:" + match_value["id"]
                writes.append(
                    Write(
                        "replace" if match else "create",
                        row_id,
                        record(pk, row_id, "video_match", match_value),
                        match["_etag"] if match else None,
                    )
                )
            link_value = dict(link["payload"]) if link else None
            if output and match_value["decision"] == "automatic":
                if link_value is None:
                    link_value = self.link_value(
                        user["user_id"], eid, video, creator["payload"], output["kind"]
                    )
                elif link_value["origin"] == "automatic" and state != "pin":
                    link_value.update(kind=output["kind"], available=True)
            elif (
                link_value
                and link_value["origin"] == "automatic"
                and state != "pin"
                and not manual
                and (not follow or follow["enabled"] or not video["available"])
            ):
                link_value["available"] = False
            if link_value:
                if not video["available"] or link_value["origin"] != "automatic" or state == "pin":
                    link_value["available"] = video["available"]
                if link_value["origin"] in {"automatic", "confirmed"}:
                    link_value["title"] = video["title"]
                if link_value["channel_id"]:
                    link_value["creator"] = creator["payload"]["name"]
                if not link or link_value != link["payload"]:
                    row_id = "link:" + link_value["id"]
                    writes.append(
                        Write(
                            "replace" if link else "create",
                            row_id,
                            record(pk, row_id, "link", link_value),
                            link["_etag"] if link else None,
                        )
                    )
        snapshot.assert_current()
        if self.rt.channels.video(channel_id, video_id)["updated_at"] != video["updated_at"] or (
            self.rt.channels.get(channel_id)["_etag"] != creator["_etag"]
        ):
            raise StoreError("CHANNEL_SNAPSHOT_CHANGED", retryable=True)
        if writes:
            job = projection_job(pk, user["revision"])
            writes.append(Write("create", job["id"], job))
        completion = (
            next_job(self.outbox, claim, after_event=event_ids[19], input_version=identity)
            if (len(event_ids) > 20)
            else self.outbox.completion(claim)
        )
        self.store.batch("state", pk, [self.accounts.guard(account), completion, *writes])

    def reviews(self, user):
        follows = {row["channel_id"]: row for row in user["config"]["creators"]}
        snapshot = self.rt.catalog.capture()
        result = []
        videos, creators = {}, {}
        rows = sorted(
            partition_items(self.store, "state", owner_partition(user["user_id"]), "video_match"),
            key=lambda row: row["payload"]["updated_at"],
            reverse=True,
        )
        for raw in rows:
            row = raw["payload"]
            follow = follows.get(row["channel_id"])
            if row["decision"] != "needs_review" or not follow:
                continue
            key = (row["channel_id"], row["video_id"])
            if key not in videos:
                videos[key] = self.rt.channels.video(*key)
            video, event = videos[key], snapshot.event(row["event_id"])
            if (
                not video
                or not video["available"]
                or not event
                or row["source_updated_at"] != video["updated_at"]
                or (row["event_updated_at"] != event.updated_at)
                or (follow["scope_keys"] and not event_keys(event).intersection(follow["scope_keys"]))
                or (row["kind"] != "unknown" and not follow.get(row["kind"], False))
            ):
                continue
            if row["channel_id"] not in creators:
                creators[row["channel_id"]] = self.rt.channels.get(row["channel_id"])["payload"]["name"]
            result.append(
                {
                    "id": row["id"],
                    "video_id": video["id"],
                    "title": video["title"],
                    "url": "https://www.youtube.com/watch?v=" + video["id"],
                    "creator": creators[row["channel_id"]],
                    "published_at": video["published_at"],
                    "event_id": event.id,
                    "event_title": event.title,
                    "starts_at": event.starts_at,
                    "kind": row["kind"],
                    "reason_codes": row["reason_codes"],
                    "rule_version": row["rule_version"],
                    "updated_at": row["updated_at"],
                }
            )
            if len(result) == 200:
                break
        snapshot.assert_current()
        return {"items": result}

    def decide(self, user_id, ident, data):
        def perform(previous):
            if len(ident) > 64:
                problem("NOT_FOUND", "未找到待确认内容", 404)
            pk = owner_partition(user_id)
            row = self.store.get("state", pk, "match:" + ident)
            if not row or row["payload"]["owner_id"] != user_id:
                problem("NOT_FOUND", "未找到待确认内容", 404)
            value = dict(row["payload"])
            if value["updated_at"] != data.expected_updated_at or value["decision"] != "needs_review":
                problem("REVIEW_CHANGED", "内容已更新，请重新查看", 409)
            follow = next(
                (
                    follow
                    for follow in previous["config"]["creators"]
                    if follow["channel_id"] == value["channel_id"]
                ),
                None,
            )
            if not follow:
                problem("REVIEW_CHANGED", "创作者已移除，请重新查看", 409)
            video = self.rt.channels.video(value["channel_id"], value["video_id"])
            event = self.rt.content.event(value["event_id"])
            if (follow["scope_keys"] and not event_keys(event).intersection(follow["scope_keys"])) or (
                value["kind"] != "unknown" and not follow.get(value["kind"], False)
            ):
                problem("REVIEW_CHANGED", "创作者关联范围已变化，请重新查看", 409)
            if not video or not video["available"]:
                problem("VIDEO_UNAVAILABLE", "视频已不可用", 409)
            if (
                video["updated_at"] != value["source_updated_at"]
                or event.updated_at != value["event_updated_at"]
            ):
                problem("REVIEW_CHANGED", "视频或比赛已变化，请重新查看", 409)
            url = "https://www.youtube.com/watch?v=" + video["id"]
            config = link_override(
                previous["config"], event.source_key, url, "pin" if data.decision == "confirm" else "block"
            )
            value.update(decision="confirmed" if data.decision == "confirm" else "ignored", updated_at=now())
            writes = [Write("replace", row["id"], record(pk, row["id"], "video_match", value), row["_etag"])]
            if data.decision == "confirm":
                existing = next(
                    (
                        row
                        for row in partition_items(self.store, "state", pk, "link")
                        if row["payload"]["event_id"] == event.id and row["payload"]["url"] == url
                    ),
                    None,
                )
                link = self.link_value(
                    user_id,
                    event.id,
                    video,
                    self.rt.channels.get(value["channel_id"])["payload"],
                    data.kind,
                    origin="confirmed",
                    old=existing["payload"] if existing else None,
                )
                row_id = "link:" + link["id"]
                writes.append(
                    Write(
                        "replace" if existing else "create",
                        row_id,
                        record(pk, row_id, "link", link),
                        existing["_etag"] if existing else None,
                    )
                )
            return Change(
                {**previous, "revision": previous["revision"] + 1, "config": config}, {"saved": True}, writes
            )

        return self.accounts.command(user_id, "review_video", {"id": ident, **data.model_dump()}, perform)
