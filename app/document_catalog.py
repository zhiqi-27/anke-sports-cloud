"""Complete provider snapshots with immutable blocks and one authoritative pointer.

Only trusted provider adapters/importers call publish; there is no public import
route. Missing rows never mean cancellation. Providers must send explicit status
changes, and a failed/incomplete fetch cannot advance the pointer or its outbox.
"""

from collections import defaultdict
from datetime import datetime
import json
import re
from types import SimpleNamespace

from app.document_accounts import document, now, projection_job
from app.document_store import Conflict, StoreError, Write, clean, encode, partition_items
from app.schemas import EventView, SourceView
from app.security import digest


def provider_partition(provider):
    if not isinstance(provider, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", provider):
        raise StoreError("CATALOG_PROVIDER_INVALID")
    return "provider:" + provider


class Catalog:
    def __init__(self, store):
        self.store = store

    def capture(self):
        roots = []
        for route in partition_items(self.store, "indexes", "catalog", "provider_route"):
            root = self.store.get("state", provider_partition(route["id"]), "schedule")
            if root:
                roots.append(root)
        return Snapshot(self, roots)

    def block(self, provider, ident):
        row = self.store.get("state", f"catalog:{provider}:{ident[:2]}", ident)
        if not row or row["kind"] != "catalog_block" or digest(encode(row["payload"]["rows"])) != ident:
            raise StoreError("CATALOG_SNAPSHOT_INCOMPLETE", retryable=True)
        return row["payload"]["rows"]

    def _blocks(self, provider, rows):
        groups, current, size = [], [], 0
        for row in sorted(rows, key=lambda r: r["id"]):
            length = len(json.dumps(row, ensure_ascii=True, allow_nan=False).encode())
            if length > 120_000:
                raise StoreError("CATALOG_ROW_TOO_LARGE")
            if current and size + length > 180_000:
                groups.append(current)
                current, size = [], 0
            current.append(row)
            size += length
        if current:
            groups.append(current)
        output = []
        for values in groups:
            ident = digest(encode(values))
            pk = f"catalog:{provider}:{ident[:2]}"
            existing = self.store.get("state", pk, ident)
            if not existing:
                row = document(pk, ident, "catalog_block", rows=values)
                try:
                    self.store.batch("state", pk, [Write("create", ident, row)])
                except Conflict:
                    pass  # An identical immutable block may have been prepared concurrently.
            self.block(provider, ident)
            output.append(ident)
        return output

    def _reserve(self, provider, category, key):
        ident = digest(category + ":" + key)
        pk = "catalog-owner:" + ident[:2]
        route = document(pk, ident, "catalog_owner", provider=provider)
        existing = self.store.get("indexes", pk, ident)
        if not existing:
            try:
                self.store.batch("indexes", pk, [Write("create", ident, route)])
            except Conflict:
                pass
            existing = self.store.get("indexes", pk, ident)
        if not existing or existing["payload"] != route["payload"]:
            raise StoreError("CATALOG_IDENTITY_CONFLICT")

    def publish(self, provider, events, sources, *, expected_revision, complete, finalize=None):
        pk = provider_partition(provider)
        if complete is not True:
            raise StoreError("PROVIDER_FETCH_INCOMPLETE", retryable=True)
        old = self.store.get("state", pk, "schedule")
        if (old["payload"]["revision"] if old else 0) != expected_revision:
            raise Conflict()
        previous = Snapshot(self, [old] if old else [])
        merged = {row.id: vars(row) for row in previous.events()}
        old_keys = {row["source_key"]: row["id"] for row in merged.values()}
        seen, seen_keys = set(), set()
        for item in events:
            row = EventView.model_validate(
                {**item, "included": False, "links": [], "description": "", "description_in_feed": False}
            ).model_dump(exclude={"included", "links", "description", "description_in_feed"})
            if row["provider"] != provider or row["id"] in seen or row["source_key"] in seen_keys:
                raise StoreError("CATALOG_IDENTITY_CONFLICT")
            if old_keys.get(row["source_key"], row["id"]) != row["id"] or (
                row["id"] in merged and merged[row["id"]]["source_key"] != row["source_key"]
            ):
                raise StoreError("CATALOG_IDENTITY_CONFLICT")
            if (
                row["starts_at"]
                and not datetime.fromisoformat(row["starts_at"].replace("Z", "+00:00")).tzinfo
            ):
                raise StoreError("CATALOG_TIME_INVALID")
            seen.add(row["id"])
            seen_keys.add(row["source_key"])
            prior = merged.get(row["id"])
            if prior and {k: v for k, v in row.items() if k != "updated_at"} == {
                k: v for k, v in prior.items() if k != "updated_at"
            }:
                row["updated_at"] = prior["updated_at"]
            elif prior:
                # A schedule cursor tracks our public revision, not the upstream
                # timestamp (which may be absent or unchanged on a correction).
                row["updated_at"] = now()
            merged[row["id"]] = row
        source_rows = {row.id: vars(row) for row in previous.sources()}
        source_seen = set()
        for item in sources:
            row = SourceView.model_validate(item).model_dump()
            if row["id"] in source_seen:
                raise StoreError("CATALOG_IDENTITY_CONFLICT")
            source_seen.add(row["id"])
            source_rows[row["id"]] = row
        # Reservations precede publication. Stale reservations can only block reuse;
        # they never expose a row or authorize a Feed read.
        for row in merged.values():
            if row["id"] not in seen or row["source_key"] in old_keys:
                continue
            self._reserve(provider, "event", row["id"])
            self._reserve(provider, "source_key", row["source_key"])
        for row in source_rows.values():
            self._reserve(provider, "source", row["id"])
        shards, months = defaultdict(list), defaultdict(list)
        for row in merged.values():
            shards[digest(row["id"])[:2]].append(row)
            month = (row["starts_at"] or row["local_date"] or "undated")[:7]
            months[month].append(row)
        content = {
            "shards": {key: self._blocks(provider, rows) for key, rows in sorted(shards.items())},
            "months": {key: self._blocks(provider, rows) for key, rows in sorted(months.items())},
            "sources": self._blocks(provider, list(source_rows.values())),
        }
        generation = digest(encode(content))
        if old and old["payload"]["generation"] == generation:
            if finalize:
                self.store.batch(
                    "state", pk, [Write("replace", "schedule", clean(old), old["_etag"]), *finalize()]
                )
            return old
        route = document("catalog", provider, "provider_route")
        try:
            self.store.batch("indexes", "catalog", [Write("create", provider, route)])
        except Conflict:
            pass
        root = document(
            pk,
            "schedule",
            "schedule",
            provider=provider,
            revision=expected_revision + 1,
            generation=generation,
            updated_at=now(),
            **content,
        )
        job = projection_job(pk, expected_revision + 1)
        job["payload"]["operation"] = "catalog_changed"
        self.store.batch(
            "state",
            pk,
            [
                Write("replace" if old else "create", "schedule", root, old["_etag"] if old else None),
                Write("create", job["id"], job),
                *(finalize() if finalize else []),
            ],
        )
        return self.store.get("state", pk, "schedule")


class Snapshot:
    def __init__(self, catalog, roots):
        self.catalog, self.roots = catalog, roots
        self._cache = {}

    def _read(self, root, blocks):
        provider = root["payload"]["provider"]
        for ident in blocks:
            key = (provider, ident)
            if key not in self._cache:
                self._cache[key] = self.catalog.block(provider, ident)
            yield from (SimpleNamespace(**row) for row in self._cache[key])

    def events(self, *, months=None):
        for root in self.roots:
            data = root["payload"]
            groups = data["shards"] if months is None else data["months"]
            for key, blocks in groups.items():
                if months is None or key in months:
                    yield from self._read(root, blocks)

    def event(self, ident):
        for root in self.roots:
            for row in self._read(root, root["payload"]["shards"].get(digest(ident)[:2], [])):
                if row.id == ident:
                    return row
        return None

    def sources(self):
        for root in self.roots:
            yield from self._read(root, root["payload"]["sources"])

    def assert_current(self):
        # Fail a stale preview/command instead of returning a mix of generations.
        current = self.catalog.capture()
        if [(r["pk"], r["payload"]["generation"]) for r in current.roots] != [
            (r["pk"], r["payload"]["generation"]) for r in self.roots
        ]:
            raise StoreError("CATALOG_SNAPSHOT_CHANGED", retryable=True)
