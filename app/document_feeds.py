"""Immutable complete Feed generations, switched by one conditional transaction."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

from app.calendar_rules import projection_from_links, select_candidates, serialize
from app.document_accounts import Accounts, Outbox, document, owner_partition
from app.document_store import StoreError, Write, clean, encode
from app.security import digest, problem


CHUNK_BYTES = 128_000  # Safe even when the SDK escapes four-byte Unicode as surrogate pairs.


class FeedGenerations:
    def __init__(self, store):
        self.store = store

    def prepare(self, pk, body, projections):
        """Chunks and their verified manifest are durable before publishing a pointer."""
        generation = uuid4().hex
        # Split encoded bytes safely, including Unicode and JSON escaping overhead.
        serialized = encode({"body": body, "projections": projections})
        chunks, current, length = [], [], 0
        for character in serialized:
            size = len(character.encode())
            if length + size > CHUNK_BYTES:
                chunks.append("".join(current))
                current, length = [], 0
            current.append(character)
            length += size
        if current:
            chunks.append("".join(current))
        if len(chunks) > 3000:
            raise StoreError("FEED_GENERATION_TOO_LARGE")
        pieces = []
        for index, text in enumerate(chunks):
            ident = f"generation:{generation}:{index:04}"
            part = document(pk, ident, "feed_chunk", text=text)
            self.store.batch("state", pk, [Write("create", ident, part)])
            pieces.append({"id": ident, "hash": digest(text)})
        manifest = document(
            pk,
            "generation:" + generation,
            "feed_manifest",
            pieces=pieces,
            hash=digest(serialized),
            byte_length=len(serialized.encode()),
        )
        self.store.batch("state", pk, [Write("create", manifest["id"], manifest)])
        # Do not point at a generation unless it can be read as one intact snapshot.
        self.load(pk, generation)
        return generation

    def load(self, pk, generation):
        if generation is None:
            return {"body": "", "projections": []}
        manifest = self.store.get("state", pk, "generation:" + generation)
        if not manifest or manifest["kind"] != "feed_manifest":
            raise StoreError("FEED_GENERATION_INCOMPLETE", retryable=True)
        texts = []
        for expected in manifest["payload"]["pieces"]:
            part = self.store.get("state", pk, expected["id"])
            if (
                not part
                or part["kind"] != "feed_chunk"
                or digest(part["payload"]["text"]) != expected["hash"]
            ):
                raise StoreError("FEED_GENERATION_INCOMPLETE", retryable=True)
            texts.append(part["payload"]["text"])
        serialized = "".join(texts)
        if digest(serialized) != manifest["payload"]["hash"] or (
            len(serialized.encode()) != manifest["payload"]["byte_length"]
        ):
            raise StoreError("FEED_GENERATION_INCOMPLETE", retryable=True)
        return json.loads(serialized)


class FeedPublisher:
    def __init__(self, store, cipher):
        self.store, self.accounts = store, Accounts(store, cipher)
        self.generations, self.outbox = FeedGenerations(store), Outbox(store)

    def publish(self, user_id, claim, events, links_for_event, *, instant=None):
        """events/links come from authoritative repositories, never from an HTTP body.

        Require an explicit link resolver so migration cannot silently drop links.
        Provider batch consistency and fan-out remain the caller's responsibility.
        """
        instant = instant or datetime.now(timezone.utc)
        pk = owner_partition(user_id)
        if claim["pk"] != pk or claim["payload"]["operation"] != "projection":
            raise StoreError("JOB_OWNER_OR_OPERATION_MISMATCH")
        account = self.accounts.active(user_id)
        feed = self.store.get("state", pk, "feed")
        self.outbox.current(claim)
        if feed["payload"]["paused"] or feed["payload"]["revoked"]:
            self.store.batch(
                "state",
                pk,
                [
                    Write("replace", "account", clean(account), account["_etag"]),
                    Write("replace", "feed", clean(feed), feed["_etag"]),
                    self.outbox.completion(claim),
                ],
            )
            return False
        config = account["payload"]["config"]
        before = self.generations.load(pk, feed["payload"]["generation"])
        existing = {value["event_id"]: SimpleNamespace(**deepcopy(value)) for value in before["projections"]}
        selected, lower, _ = select_candidates(events, config, existing, instant)
        wanted = set()
        for event in selected:
            wanted.add(event.id)
            data = projection_from_links(event, links_for_event(event), config)
            # Keep the existing SQL hash algorithm and any imported projection IDs.
            content_hash = digest(json.dumps(data, sort_keys=True, ensure_ascii=False))
            projection = existing.get(event.id)
            if projection is None:
                stable_id = digest(feed["payload"]["feed_id"] + ":" + event.id)[:32]
                projection = SimpleNamespace(
                    id=stable_id,
                    feed_id=feed["payload"]["feed_id"],
                    event_id=event.id,
                    version=0,
                    content_hash="",
                    data={},
                    updated_at=instant.isoformat(),
                    removed=False,
                )
                existing[event.id] = projection
            if projection.content_hash != content_hash or projection.removed:
                projection.data, projection.content_hash = data, content_hash
                projection.version += 1
                projection.updated_at, projection.removed = instant.isoformat(), False
        for event_id, projection in existing.items():
            if event_id not in wanted and not projection.removed:
                projection.removed = True
                projection.version += 1
                projection.updated_at = instant.isoformat()
        retained = [
            value for value in existing.values() if not value.removed or value.updated_at[:10] >= lower
        ]
        body = serialize(retained).decode()
        etag = digest(body)
        changed = feed["payload"]["etag"] != etag
        writes = [Write("replace", "account", clean(account), account["_etag"])]
        if changed:
            generation = self.generations.prepare(pk, body, [vars(value) for value in retained])
            updated = clean(feed)
            updated["payload"] = {
                **feed["payload"],
                "generation": generation,
                "etag": etag,
                "revision": feed["payload"]["revision"] + 1,
                "updated_at": instant.isoformat(),
                "event_count": sum(not value.removed for value in retained),
            }
            writes.append(Write("replace", "feed", updated, feed["_etag"]))
        else:
            # Validate the captured pointer/paused/token state without changing public versions.
            writes.append(Write("replace", "feed", clean(feed), feed["_etag"]))
        writes.append(self.outbox.completion(claim))
        # Config change, deletion, rotation, or another publisher invalidates this complete batch.
        self.store.batch("state", pk, writes)
        return changed

    def read(self, token):
        feed = self.accounts.feed_for_token(token)
        snapshot = self.generations.load(feed["pk"], feed["payload"]["generation"])
        if not snapshot["body"]:
            problem("FEED_BUILDING", "订阅源尚未发布，请稍后重试", 503)
        # Concurrent revocation must be checked after the potentially large chunk read as well.
        current = self.accounts.feed_for_token(token)
        if current["payload"]["generation"] != feed["payload"]["generation"]:
            raise StoreError("FEED_SNAPSHOT_CHANGED", retryable=True)
        return SimpleNamespace(**feed["payload"], body=snapshot["body"])
