"""Personal broadcast links and configuration import commands."""

from types import SimpleNamespace

from app.config_rules import confirm_import, import_configuration, link_override
from app.document_accounts import Change, document, now, owner_partition
from app.document_store import StoreError, Write, partition_items
from app.link_rules import selected_links
from app.security import canonical_url, digest, problem


class Content:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store, self.accounts = runtime.store, runtime.accounts

    def rows(self, user_id):
        return [
            SimpleNamespace(**row["payload"])
            for row in partition_items(self.store, "state", owner_partition(user_id), "link")
        ]

    def selected(self, event, payload, *, rows=None):
        public = self.runtime.broadcasts.selected(event, payload)
        if payload is None:
            return public
        rows = self.rows(payload["user_id"]) if rows is None else rows
        return public + selected_links(
            event,
            payload["config"],
            [row for row in rows if row.event_id == event.id],
            lambda _: None,
            payload["user_id"],
        )

    def event(self, ident):
        event = self.runtime.catalog.capture().event(ident)
        if not event:
            problem("EVENT_NOT_FOUND", "未找到这场比赛", 404)
        return event

    def owned_link(self, user_id, ident):
        if len(ident) > 64:
            problem("NOT_FOUND", "未找到此链接", 404)
        row = self.store.get("state", owner_partition(user_id), "link:" + ident)
        if not row or row["kind"] != "link" or row["payload"]["owner_id"] != user_id:
            problem("NOT_FOUND", "未找到此链接", 404)
        return row

    def attach(self, user_id, event_id, data, key):
        def perform(previous):
            event = self.event(event_id)
            url, platform = canonical_url(data.url)
            pk = owner_partition(user_id)
            ident = digest(user_id + ":" + event_id + ":" + url)[:32]
            row_id = "link:" + ident
            old = self.store.get("state", pk, row_id)
            if old and any(
                old["payload"][name] != expected
                for name, expected in {
                    "owner_id": user_id,
                    "event_id": event_id,
                    "url": url,
                }.items()
            ):
                raise StoreError("LINK_IDENTITY_CONFLICT")
            value = (
                dict(old["payload"])
                if old
                else {
                    "id": ident,
                    "owner_id": user_id,
                    "event_id": event_id,
                    "url": url,
                    "url_hash": digest(url),
                    "title": f"{platform} 原链接",
                    "platform": platform,
                    "access": "unknown",
                    "regions": [],
                    "created_at": now(),
                }
            )
            value.update(
                title=data.title.strip() or value["title"], kind=data.kind, origin="manual", available=True
            )
            blocked = any(
                row["event_key"] == event.source_key and row["url"] == url and row["state"] == "block"
                for row in previous["config"]["link_overrides"]
            )
            config = (
                previous["config"]
                if blocked
                else link_override(previous["config"], event.source_key, url, "pin")
            )
            updated = {**previous, "config": config, "revision": previous["revision"] + (not blocked)}
            rows = [row for row in self.rows(user_id) if row.id != ident] + [SimpleNamespace(**value)]
            result = {"id": ident, "event": self.runtime.event_view(event, updated, link_rows=rows)}
            row = document(pk, row_id, "link")
            row["payload"] = value
            return Change(
                updated,
                result,
                [Write("replace" if old else "create", row_id, row, old["_etag"] if old else None)],
            )

        return self.accounts.command(
            user_id, "attach_event_link", {"event_id": event_id, **data.model_dump()}, perform, key=key
        )

    def override_link(self, user_id, ident, state, key=None):
        def perform(previous):
            public = self.store.get("state", self.runtime.broadcasts.pk, ident)
            if public and public["kind"] == "broadcast" and public["payload"]["published"]:
                link = public["payload"]["published"]
            else:
                link = self.owned_link(user_id, ident)["payload"]
            event = self.event(link["event_id"])
            config = link_override(previous["config"], event.source_key, link["url"], state)
            return Change(
                {**previous, "config": config, "revision": previous["revision"] + 1},
                {"blocked" if state == "block" else "pinned": True},
            )

        return self.accounts.command(
            user_id,
            "remove_event_link" if state == "block" else "pin_event_link",
            {"link_id": ident},
            perform,
            key=key,
        )

    def preview_import(self, previous, data):
        if previous["revision"] != data.expected_revision:
            problem("REVISION_CONFLICT", "配置已更新，请重新预览", 409)
        snapshot = self.runtime.catalog.capture()
        sources = {row.id for row in snapshot.sources()}
        events = {key for row in snapshot.events() for key in (row.id, row.source_key)}
        config, preview = import_configuration(
            SimpleNamespace(id=previous["user_id"], **previous),
            data,
            self.runtime.cfg.cipher(),
            source_exists=sources.__contains__,
            event_exists=events.__contains__,
        )
        snapshot.assert_current()
        return config, preview

    def import_config(self, user_id, data, key):
        if data.dry_run:
            _, preview = self.preview_import(self.accounts.active(user_id)["payload"], data)
            return {**preview, "applied": False}

        def perform(previous):
            config, preview = self.preview_import(previous, data)
            confirm_import(data, preview, self.runtime.cfg.cipher())
            return Change(
                {**previous, "config": config, "revision": previous["revision"] + 1},
                {**preview, "applied": True},
            )

        return self.accounts.command(user_id, "import_config", data.model_dump(), perform, key=key)
