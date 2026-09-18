"""Account-independent source feeds, published only by durable background work."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

from app.calendar_rules import projection_from_links, select_candidates, serialize
from app.document_accounts import Outbox, document, now, projection_job
from app.document_feeds import FeedGenerations
from app.document_store import Write, clean, partition_items
from app.schemas import Config
from app.security import digest, problem


class PublicFeeds:
    def __init__(self, runtime):
        self.rt = runtime
        self.pk = "provider:public-feeds"
        self.generations = FeedGenerations(runtime.store)

    def allowed(self, source):
        return self.rt.cfg.env == "local" or (
            not source.demo and source.id in self.rt.cfg.public_feed_source_keys
        )

    def source(self, key):
        return next((s for s in self.rt.catalog.capture().sources() if s.id == key), None)

    def ident(self, key):
        return "public_" + digest(key)[:48]

    def schedule(self, trigger):
        for source in self.rt.catalog.capture().sources():
            if not self.allowed(source):
                continue
            ident = self.ident(source.id)
            job = projection_job(self.pk, 0)
            job["id"] = "job:" + digest(trigger + ":" + ident)[:32]
            if self.rt.store.get("state", self.pk, job["id"]):
                continue
            job["payload"].update(operation="public_projection", source_id=source.id)
            self.rt.store.batch("state", self.pk, [Write("create", job["id"], job)])

    def info(self, key):
        source = self.source(key)
        if not source:
            problem("SOURCE_NOT_FOUND", "未找到球队或赛事", 404)
        row = self.rt.store.get("state", self.pk, self.ident(key))
        value = row["payload"] if row else {}
        available = self.allowed(source)
        published = available and value.get("generation")
        jobs = [
            r
            for r in partition_items(self.rt.store, "state", self.pk, "outbox")
            if r["payload"].get("source_id") == key
        ]
        pending = any(r["state"] in {"pending", "running"} for r in jobs)
        finished = sorted(
            (r for r in jobs if r["state"] in {"done", "failed"}),
            key=lambda r: (r.get("finished_at", ""), r["id"]),
        )
        failed = bool(finished and finished[-1]["state"] == "failed")
        return dict(
            source_id=key,
            name=source.name,
            demo=source.demo,
            status="unavailable"
            if not available
            else "updating"
            if pending
            else "error"
            if failed
            else "published"
            if published
            else "pending",
            url=self.rt.cfg.public_url.rstrip("/") + "/public-feeds/" + row["id"] + ".ics"
            if published
            else None,
            revision=value.get("revision", 0) if published else 0,
            updated_at=value.get("updated_at") if published else None,
            event_count=value.get("event_count", 0) if published else 0,
            local_only=self.rt.cfg.env == "local",
        )

    def publish(self, claim):
        rt = self.rt
        snapshot = rt.catalog.capture()
        key = claim["payload"]["source_id"]
        source = next((s for s in snapshot.sources() if s.id == key), None)
        outbox = Outbox(rt.store)
        if not source or not self.allowed(source):
            rt.store.batch("state", self.pk, [outbox.completion(claim)])
            return
        ident = self.ident(key)
        old = rt.store.get("state", self.pk, ident)
        value = old["payload"] if old else dict(source_id=key, generation=None, revision=0, etag="")
        before = self.generations.load(self.pk, value["generation"])
        existing = {p["event_id"]: SimpleNamespace(**deepcopy(p)) for p in before["projections"]}
        config = Config().model_dump()
        config["follows"] = [{"type": "team" if source.kind == "team" else "competition", "source_key": key}]
        instant = datetime.now(timezone.utc)
        selected, lower, _ = select_candidates(list(snapshot.events()), config, existing, instant)
        wanted = set()
        for event in selected:
            wanted.add(event.id)
            links = rt.broadcasts.selected(event, None) if hasattr(rt, "broadcasts") else []
            data = projection_from_links(event, links, config, personal=False)
            hashed = digest(json.dumps(data, sort_keys=True, ensure_ascii=False))
            projection = existing.get(event.id)
            if not projection:
                projection = SimpleNamespace(
                    id=digest(ident + ":" + event.id)[:32],
                    feed_id=ident,
                    event_id=event.id,
                    version=0,
                    content_hash="",
                    data={},
                    updated_at=instant.isoformat(),
                    removed=False,
                )
                existing[event.id] = projection
            if projection.content_hash != hashed or projection.removed:
                projection.data, projection.content_hash = data, hashed
                projection.version += 1
                projection.updated_at, projection.removed = instant.isoformat(), False
        for eid, projection in existing.items():
            if eid not in wanted and not projection.removed:
                projection.removed = True
                projection.version += 1
                projection.updated_at = instant.isoformat()
        retained = [p for p in existing.values() if not p.removed or p.updated_at[:10] >= lower]
        body = serialize(retained).decode()
        etag = digest(body)
        writes = []
        if etag != value["etag"]:
            generation = self.generations.prepare(self.pk, body, [vars(p) for p in retained])
            row = document(
                self.pk,
                ident,
                "public_feed",
                source_id=key,
                generation=generation,
                etag=etag,
                revision=value["revision"] + 1,
                updated_at=now(),
                event_count=sum(not p.removed for p in retained),
            )
            writes.append(Write("replace" if old else "create", ident, row, old["_etag"] if old else None))
        elif old:
            writes.append(Write("replace", ident, clean(old), old["_etag"]))
        snapshot.assert_current()
        rt.store.batch("state", self.pk, [*writes, outbox.completion(claim)])

    def read(self, ident):
        row = self.rt.store.get("state", self.pk, ident)
        source = self.source(row["payload"]["source_id"]) if row else None
        if not source or not self.allowed(source):
            problem("PUBLIC_FEED_NOT_FOUND", "未找到可订阅的公共日历", 404)
        data = self.generations.load(self.pk, row["payload"]["generation"])
        if not data["body"]:
            problem("FEED_BUILDING", "订阅源尚未发布", 503)
        return SimpleNamespace(**row["payload"], body=data["body"], paused=False, revoked=False)
