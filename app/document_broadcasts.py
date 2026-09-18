"""Reviewed broadcast publications, audit and fanout share one transaction."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.broadcast_rules import normalized, public_metadata, validate_rights
from app.document_accounts import Outbox, document, now, projection_job
from app.document_store import Write, clean, partition_items
from app.link_rules import selected_links
from app.platforms import candidate_url, head_probe, selected_product
from app.schemas import Config
from app.security import digest, problem


class Broadcasts:
    pk = "provider:broadcasts"

    def __init__(self, runtime):
        self.rt = runtime

    def get(self, ident):
        row = self.rt.store.get("state", self.pk, ident)
        if not row or row["kind"] != "broadcast":
            problem("BROADCAST_NOT_FOUND", "未找到直播审核记录", 404)
        return row

    def view(self, row):
        value = row["payload"]
        event = self.rt.catalog.capture().event(value["draft"]["event_id"])
        return {
            **value,
            "id": row["id"],
            "event_title": event.title if event else "赛事已不可用",
            "event_demo": event.demo if event else False,
            "draft_changed": not value["published"]
            or any(value["published"].get(k) != v for k, v in value["draft"].items()),
        }

    def save(self, row, old, actor, action, detail, *, publish=False):
        value = row["payload"]
        value["revision"] = old["payload"]["revision"] + 1 if old else 0
        value["audit"] = [
            {"action": action, "revision": value["revision"], "detail": detail, "created_at": now()},
            *value.get("audit", []),
        ][:30]
        audit = document(
            self.pk,
            "audit:" + uuid4().hex,
            "broadcast_audit",
            actor_id=actor,
            record_id=row["id"],
            **value["audit"][0],
        )
        writes = [
            Write("replace" if old else "create", row["id"], row, old["_etag"] if old else None),
            Write("create", audit["id"], audit),
        ]
        if publish:
            job = projection_job(self.pk, value["revision"])
            job["payload"]["operation"] = "catalog_changed"
            writes.append(Write("create", job["id"], job))
        self.rt.store.batch("state", self.pk, writes)
        return self.view(row)

    def create(self, actor, data):
        value = normalized(data.model_dump())
        if not self.rt.catalog.capture().event(value["event_id"]):
            problem("EVENT_NOT_FOUND", "请选择已存在的比赛", 404)
        ident = digest(value["event_id"] + ":" + value["url"])[:32]
        if self.rt.store.get("state", self.pk, ident):
            problem("BROADCAST_EXISTS", "本场已有这个链接", 409)
        row = document(
            self.pk,
            ident,
            "broadcast",
            draft=value,
            published=None,
            published_revision=None,
            status="draft",
            network_status="not_checked",
            network_checked_at=None,
            next_check_at=now(),
            device_tests=[],
            missing_count=0,
        )
        return self.save(row, None, actor, "draft_created", {"draft": value})

    def change(self, actor, ident, action, data):
        old = self.get(ident)
        if data.expected_revision != old["payload"]["revision"]:
            problem("REVISION_CONFLICT", "记录已更新，请重新读取", 409)
        row = clean(old)
        v = row["payload"]
        instant = datetime.now(timezone.utc)
        if action == "edit":
            draft = normalized(data.model_dump(exclude={"expected_revision"}))
            if draft["event_id"] != v["draft"]["event_id"]:
                problem("EVENT_IMMUTABLE", "记录绑定的比赛不可修改")
            v["draft"] = draft
        elif action == "publish":
            if not data.source_and_event_confirmed:
                problem("EVIDENCE_REQUIRED", "必须核对官方来源与具体场次")
            if not instant < data.valid_until <= instant + timedelta(days=7):
                problem("INVALID_VALIDITY", "请设置未来7天内的复查期限")
            event = self.rt.catalog.capture().event(v["draft"]["event_id"])
            if not event:
                problem("EVENT_NOT_FOUND", "请选择已存在的比赛", 404)
            validate_rights(v["draft"], event.competition_id)
            if (
                not v["published"]
                or v["published"]["url"] != v["draft"]["url"]
                or v["published"]["content_type"] != v["draft"]["content_type"]
            ):
                v.update(device_tests=[], network_status="not_checked", network_checked_at=None)
            v.update(
                published={
                    **v["draft"],
                    "reviewed_at": instant.isoformat(),
                    "valid_until": data.valid_until.isoformat(),
                },
                published_revision=v["revision"] + 1,
                status="published",
                next_check_at=now(),
                missing_count=0,
            )
        elif action == "suspend":
            v["status"] = "suspended"
        elif action == "device-evidence":
            if v["status"] != "published":
                problem("PUBLICATION_REQUIRED", "请先审核发布")
            evidence = data.model_dump(mode="json", exclude={"expected_revision"})
            evidence["url_hash"] = digest(v["published"]["url"])
            v["device_tests"] = [*v["device_tests"][-19:], evidence]
        elif action == "check":
            if v["status"] != "published":
                problem("PUBLICATION_REQUIRED", "请先审核发布")
            v["next_check_at"] = now()
        return self.save(
            row,
            old,
            actor,
            action,
            data.model_dump(mode="json"),
            publish=action in {"publish", "suspend", "device-evidence"},
        )

    def selected(self, event, payload):
        links, metadata = [], {}
        config = payload["config"] if payload else Config().model_dump()
        region = config["preferences"].get("watch_region")
        for row in partition_items(self.rt.store, "state", self.pk, "broadcast"):
            value = row["payload"]
            pub = value["published"]
            if (
                value["status"] != "published"
                or not pub
                or pub["event_id"] != event.id
                or pub["valid_until"] <= now()
            ):
                continue
            if region and (
                (pub["region_mode"] == "include" and region not in pub["regions"])
                or (pub["region_mode"] == "exclude" and region in pub["regions"])
            ):
                continue
            metadata[row["id"]] = public_metadata(SimpleNamespace(**value))
            links.append(
                SimpleNamespace(
                    id=row["id"],
                    owner_id="public",
                    available=True,
                    url=pub["url"],
                    title=pub["title"],
                    kind="watch_along" if pub["content_type"] == "watch_along" else "live",
                    platform=candidate_url(pub["url"])[1],
                    creator="",
                    origin="official",
                    access=pub["access"],
                    regions=pub["regions"] if pub["region_mode"] == "include" else [],
                    created_at=pub["reviewed_at"],
                )
            )
        product = selected_product(event, config)
        if product and product["metadata"]["platform_id"] not in {
            value["platform_id"] for value in metadata.values()
        }:
            metadata[product["id"]] = product["metadata"]
            links.append(
                SimpleNamespace(
                    id=product["id"],
                    owner_id="public",
                    available=True,
                    url=product["url"],
                    title=product["title"],
                    kind=product["kind"],
                    platform=product["platform"],
                    creator="",
                    origin=product["origin"],
                    access=product["access"],
                    regions=product["regions"],
                    created_at=product["created_at"],
                )
            )
        return selected_links(
            event, config, links, lambda link: metadata[link.id], payload["user_id"] if payload else None
        )

    def schedule(self):
        for row in partition_items(self.rt.store, "state", self.pk, "broadcast"):
            value = row["payload"]
            if value["status"] != "published":
                continue
            if value["published"]["valid_until"] <= now():
                expired = clean(row)
                expired["payload"]["status"] = "expired"
                self.save(expired, row, "worker", "expired", {}, publish=True)
            elif self.rt.cfg.broadcast_checks_enabled and value["next_check_at"] <= now():
                job = projection_job(self.pk, value["revision"])
                job["payload"].update(
                    operation="broadcast_check",
                    record_id=row["id"],
                    url_hash=digest(value["published"]["url"]),
                )
                changed = clean(row)
                changed["payload"]["next_check_at"] = (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat()
                self.rt.store.batch(
                    "state",
                    self.pk,
                    [Write("replace", row["id"], changed, row["_etag"]), Write("create", job["id"], job)],
                )

    def check(self, claim):
        old = self.get(claim["payload"]["record_id"])
        value = old["payload"]
        if (
            value["status"] == "published"
            and digest(value["published"]["url"]) == claim["payload"]["url_hash"]
        ):
            outcome = head_probe(value["published"]["url"])
            row = clean(old)
            v = row["payload"]
            if outcome == "not_found" and (
                not v["network_checked_at"]
                or datetime.now(timezone.utc) - datetime.fromisoformat(v["network_checked_at"])
                >= timedelta(minutes=5)
            ):
                v["missing_count"] += 1
            elif outcome == "reachable":
                v["missing_count"] = 0
            v.update(
                network_status=outcome,
                network_checked_at=now(),
                next_check_at=(
                    datetime.now(timezone.utc)
                    + timedelta(hours=1 if outcome in {"retry", "not_found"} else 6)
                ).isoformat(),
            )
            withdraw = outcome in {"unsafe", "redirect_review"} or v["missing_count"] >= 2
            if withdraw:
                v["status"] = "needs_review"
            self.save(
                row,
                old,
                "worker",
                "network_checked",
                {"outcome": outcome, "http_only": True},
                publish=withdraw,
            )
        self.rt.store.batch("state", self.pk, [Outbox(self.rt.store).completion(claim)])
