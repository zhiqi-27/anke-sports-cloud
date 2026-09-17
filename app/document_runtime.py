"""Calendar services for the document runtime, independent of HTTP and queues."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.calendar_rules import calendar_membership, describe, included, select_candidates
from app.calendar_commands import add_manual_source, remove_manual_source, validate_manual_event
from app.document_accounts import Accounts, Change, owner_partition
from app.document_catalog import Catalog
from app.document_feeds import FeedPublisher
from app.document_store import StoreError, partition_items
from app.follow_rules import direct_follow_allowed, follow_impact
from app.schedule_rules import schedule_page, schedule_range
from app.schemas import Config
from app.security import problem


def user_value(payload):
    return SimpleNamespace(id=payload["user_id"], **payload)


class Runtime:
    def __init__(self, store, cfg):
        from app.document_content import Content
        from app.document_providers import Providers

        self.store, self.cfg = store, cfg
        self.accounts = Accounts(store, cfg.cipher())
        self.catalog = Catalog(store)
        self.publisher = FeedPublisher(store, cfg.cipher())
        self.content = Content(self)
        self.providers = Providers(self)
        from app.document_privacy import Privacy

        self.privacy = Privacy(self)
        from app.document_oauth import DocumentOAuthProvider

        self.oauth = DocumentOAuthProvider(self)
        from app.document_public_feeds import PublicFeeds

        self.public_feeds = PublicFeeds(self)
        from app.document_broadcasts import Broadcasts

        self.broadcasts = Broadcasts(self)

    def calendar_supported(self, payload):
        Config.model_validate(payload["config"])

    def activity(self, pk):
        pending, last = False, None
        for row in partition_items(self.store, "state", pk, "outbox"):
            if row["payload"]["operation"] != "projection":
                continue
            pending |= row["state"] in {"pending", "running"}
            if row["state"] in {"done", "failed"} and (
                not last
                or (row.get("finished_at", ""), row["id"]) > (last.get("finished_at", ""), last["id"])
            ):
                last = row
        return pending, last

    def user_view(self, payload, *, pending=None):
        pk = owner_partition(payload["user_id"])
        feed = self.store.get("state", pk, "feed")["payload"]
        active, last = self.activity(pk)
        active = active if pending is None else pending
        failed = last and last["state"] == "failed" and last.get("finished_at", "") > feed["updated_at"]
        return {
            "id": payload["user_id"],
            "display_name": payload["display_name"],
            "is_maintainer": payload["user_id"] in self.cfg.maintainer_ids,
            "revision": payload["revision"],
            "config": payload["config"],
            "feed": {
                "revision": feed["revision"],
                "updated_at": feed["updated_at"],
                "paused": feed["paused"],
                "status": "updating"
                if active
                else "error"
                if failed
                else "published"
                if feed["generation"]
                else "pending",
                "event_count": feed["event_count"],
            },
        }

    def event_view(self, event, payload=None, *, link_rows=None, content_validated=False):
        if payload and not content_validated:
            self.calendar_supported(payload)
        config = payload["config"] if payload else Config().model_dump()
        selected = included(event, config) if payload else False
        links = self.content.selected(event, payload, rows=link_rows)
        return {
            **vars(event),
            "included": selected,
            "calendar": calendar_membership(event, config) if payload else None,
            "links": links,
            "description": describe(event, links, config),
            "description_in_feed": selected,
        }

    def schedule(self, from_, to, dataset, followed, q, payload, limit, cursor, source_id=""):
        if payload:
            self.calendar_supported(payload)
        lower, upper, earliest, latest = schedule_range(from_, to, dataset, q, limit)
        # Month blocks cover the conservative UTC envelope and date-only values.
        first = min(earliest[:7], lower.date().isoformat()[:7])
        last = max((latest or "9999-12")[:7], upper.date().isoformat()[:7])
        snapshot = self.catalog.capture()
        months = {key for root in snapshot.roots for key in root["payload"]["months"] if first <= key <= last}
        page, next_cursor = schedule_page(
            snapshot.events(months=months),
            from_,
            to,
            dataset,
            followed,
            q,
            user_value(payload) if payload else None,
            limit,
            cursor,
            source_id,
        )
        link_rows = self.content.rows(payload["user_id"]) if payload else []
        result = {
            "items": [
                self.event_view(row, payload, link_rows=link_rows, content_validated=True) for row in page
            ],
            "next_cursor": next_cursor,
            "coverage": {
                "dataset": dataset,
                "complete": dataset == "demo",
                "note": "synthetic fixtures"
                if dataset == "demo"
                else "Only connected provider snapshots; coverage is not guaranteed",
            },
        }
        snapshot.assert_current()
        return result

    def follows(self, payload, data, *, preview=False):
        self.calendar_supported(payload)
        if payload["revision"] != data.expected_revision:
            problem("REVISION_CONFLICT", "配置已在其他页面更新，请刷新后重试", 409)
        snapshot = self.catalog.capture()
        sources = {row.id: row for row in snapshot.sources()}
        events = {row.id: row for row in snapshot.events()}
        for follow in data.follows:
            source = sources.get(follow.source_key)
            valid_source = source and source.kind == follow.type
            if not valid_source:
                problem("SOURCE_NOT_FOUND", "该关注对象或类型尚未接入")
            if valid_source and not direct_follow_allowed(source):
                problem("FOLLOW_SCOPE_NOT_ALLOWED", "英超和 NBA 等联赛请按球队关注")
        unique = {row.source_key: row.model_dump() for row in data.follows}
        config = {**payload["config"], "follows": [unique[key] for key in sorted(unique)]}
        if preview or data.confirmation:
            pk = owner_partition(payload["user_id"])
            feed = self.store.get("state", pk, "feed")["payload"]
            previous = self.publisher.generations.load(pk, feed["generation"])
            existing = {row["event_id"]: SimpleNamespace(**row) for row in previous["projections"]}
            instant = datetime.now(timezone.utc)
            selected, lower, upper = select_candidates(events.values(), config, existing, instant)
            impact = follow_impact(
                user_value(payload),
                config,
                SimpleNamespace(**feed),
                existing,
                selected,
                lower,
                upper,
                instant,
                source_for_key=sources.get,
                event_by_id=events.get,
                all_events=events.values(),
                publication_pending=self.activity(pk)[0],
            )
            if not preview and impact["confirmation"] != data.confirmation:
                problem("FOLLOWS_PREVIEW_CHANGED", "赛程或订阅内容已变化，请重新预览后保存", 409)
        snapshot.assert_current()
        return impact if preview else config

    def save_follows(self, user_id, data, key):
        return self.accounts.save_config(
            user_id,
            None,
            data.expected_revision,
            key=key,
            operation="set_follows",
            payload=data.model_dump(exclude_none=True),
            prepare=lambda current: self.follows(current, data),
            response=lambda updated: self.user_view(updated, pending=True),
        )

    def add_calendar_event(self, user_id, event_id, data, key):
        def perform(previous):
            if previous["revision"] != data.expected_revision:
                problem("REVISION_CONFLICT", "配置已在其他页面更新，请刷新后重试", 409)
            snapshot = self.catalog.capture()
            event = snapshot.event(event_id)
            validate_manual_event(event, environment=self.cfg.env)
            config, changed = add_manual_source(previous["config"], event.id)
            snapshot.assert_current()
            updated = (
                {**previous, "config": config, "revision": previous["revision"] + 1}
                if changed
                else previous
            )
            return Change(
                updated,
                self.user_view(
                    updated,
                    pending=changed or self.activity(owner_partition(previous["user_id"]))[0],
                ),
            )

        return self.accounts.command(
            user_id,
            "add_calendar_event",
            {"event_id": event_id, "expected_revision": data.expected_revision},
            perform,
            key=key,
        )

    def remove_calendar_event(self, user_id, event_id, data, key):
        def perform(previous):
            if previous["revision"] != data.expected_revision:
                problem("REVISION_CONFLICT", "配置已在其他页面更新，请刷新后重试", 409)
            snapshot = self.catalog.capture()
            event = snapshot.event(event_id)
            validate_manual_event(event, environment=self.cfg.env)
            config, changed = remove_manual_source(previous["config"], event)
            snapshot.assert_current()
            updated = {**previous, "config": config, "revision": previous["revision"] + 1}
            return Change(updated, self.user_view(updated, pending=True))

        return self.accounts.command(
            user_id,
            "remove_calendar_event",
            {"event_id": event_id, "expected_revision": data.expected_revision},
            perform,
            key=key,
        )

    def publish(self, claim):
        account = self.store.get("state", claim["pk"], "account")
        if not account or account["payload"]["deleted"]:
            raise StoreError("ACCOUNT_DELETED")
        account = self.accounts.active(account["payload"]["user_id"])
        self.calendar_supported(account["payload"])
        snapshot = self.catalog.capture()
        # Narrow to the publication window; undated rows cannot enter an ICS.
        instant = datetime.now(timezone.utc)
        lower = (instant - timedelta(days=91)).date().isoformat()[:7]
        upper = (instant + timedelta(days=181)).date().isoformat()[:7]
        months = {
            key for root in snapshot.roots for key in root["payload"]["months"] if lower <= key <= upper
        }

        def validate():
            snapshot.assert_current()
            self.calendar_supported(self.accounts.active(account["payload"]["user_id"])["payload"])

        link_rows = self.content.rows(account["payload"]["user_id"])
        return self.publisher.publish(
            account["payload"]["user_id"],
            claim,
            list(snapshot.events(months=months)),
            lambda event: self.content.selected(event, account["payload"], rows=link_rows),
            instant=instant,
            validate_snapshot=validate,
            expected_account_etag=account["_etag"],
        )
