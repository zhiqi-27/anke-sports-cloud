import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, false, func, or_, select, update

from app.db import BroadcastRecord, Event, Feed, Link, Projection, User, now
from app.schemas import Config
from app.security import digest
from app.calendar_rules import (
    select_candidates, projection_from_links,
    event_keys as event_keys, inclusion_filter as inclusion_filter, included as included,
    delivery_links as delivery_links, describe as describe, serialize as serialize, event_is_past as event_is_past,
)


def load_links(db, events, user):
    """Request-local rows, owner-filtered before loading. Never cache across actors."""
    by_event, broadcasts = defaultdict(list), {}
    ids = [event.id for event in events]
    owners = ["public"] + ([user.id] if user else [])
    # Bound SQL parameters for large Feed publications as well as HTTP pages.
    for offset in range(0, len(ids), 400):
        rows = db.scalars(
            select(Link).where(
                Link.event_id.in_(ids[offset : offset + 400]),
                Link.owner_id.in_(owners),
                Link.available.is_(True),
            )
        ).all()
        for row in rows:
            by_event[row.event_id].append(row)
        public = [row.id for row in rows if row.owner_id == "public"]
        for start in range(0, len(public), 400):
            broadcasts.update(
                (row.link_id, row)
                for row in db.scalars(
                    select(BroadcastRecord).where(BroadcastRecord.link_id.in_(public[start : start + 400]))
                )
            )
    return by_event, broadcasts


def chosen_links(db, event: Event, user: User | None, *, rows=None, broadcasts=None) -> list[dict]:
    config = user.config if user else Config().model_dump()
    owners = ["public"] + ([user.id] if user else [])
    links = (
        rows
        if rows is not None
        else db.scalars(
            select(Link).where(Link.event_id == event.id, Link.owner_id.in_(owners), Link.available.is_(True))
        ).all()
    )
    overrides = {
        x["url"]: x["state"] for x in config.get("link_overrides", []) if x["event_key"] == event.source_key
    }
    creators = {x["channel_id"]: x for x in config.get("creators", [])}
    result = []
    for link in links:
        broadcast = None
        if link.owner_id == "public":
            record = broadcasts.get(link.id) if broadcasts is not None else db.get(BroadcastRecord, link.id)
            if not record or record.status != "published" or not record.published:
                continue
            # Only the reviewed publication is authoritative, never an edited draft.
            if record.published["url"] != link.url:
                continue
            region = config.get("preferences", {}).get("watch_region")
            if (
                region
                and record.published["region_mode"] == "exclude"
                and region in record.published["regions"]
            ):
                continue
            from app.broadcasts import public_metadata

            broadcast = public_metadata(record)
        state = overrides.get(link.url)
        if state == "block":
            continue
        if link.origin == "automatic" and state != "pin":
            creator = creators.get(link.channel_id)
            if not creator or not creator.get(link.kind, False):
                continue
            if creator["scope_keys"] and not event_keys(event).intersection(creator["scope_keys"]):
                continue
        region = config.get("preferences", {}).get("watch_region")
        if region and link.regions and region not in link.regions:
            continue
        result.append(
            {
                "id": link.id,
                "broadcast": broadcast,
                "url": link.url,
                "title": link.title,
                "kind": link.kind,
                "platform": link.platform,
                "creator": link.creator,
                "origin": link.origin,
                "access": link.access,
                "regions": link.regions,
                "pinned": state == "pin",
                "created_at": link.created_at,
            }
        )
    result.sort(
        key=lambda x: (not x["pinned"], x["origin"] != "official", x["creator"], x["created_at"], x["id"])
    )
    unique, seen_urls = [], set()
    for item in result:
        if item["url"] not in seen_urls:
            unique.append(item)
            seen_urls.add(item["url"])
    return unique


def event_view(db, event: Event, user: User | None = None, *, link_rows=None, broadcasts=None) -> dict:
    links = chosen_links(db, event, user, rows=link_rows, broadcasts=broadcasts)
    config = user.config if user else Config().model_dump()
    return {
        "id": event.id,
        "source_key": event.source_key,
        "competition_id": event.competition_id,
        "sport": event.sport,
        "title": event.title,
        "starts_at": event.starts_at,
        "local_date": event.local_date,
        "time_precision": event.time_precision,
        "timezone": event.timezone,
        "duration": event.duration,
        "venue": event.venue,
        "status": event.status,
        "participants": event.participants,
        "provider": event.provider,
        "source_url": event.source_url,
        "updated_at": event.updated_at,
        "demo": event.demo,
        "included": included(event, config) if user else False,
        "links": links,
        "description": describe(event, links, config),
        "description_in_feed": included(event, config) if user else False,
    }


def projection_data(db, event, user, config, *, link_rows=None, broadcasts=None):
    links = chosen_links(db, event, user, rows=link_rows, broadcasts=broadcasts)
    return projection_from_links(event, links, config)


def update_projection(db, feed, event, data, existing):
    content_hash = digest(json.dumps(data, sort_keys=True, ensure_ascii=False))
    projection = existing.get(event.id)
    if not projection:
        projection = Projection(feed_id=feed.id, event_id=event.id, version=0)
        db.add(projection)
        existing[event.id] = projection
    if projection.content_hash != content_hash or projection.removed:
        projection.data = data
        projection.content_hash = content_hash
        projection.version += 1
        projection.updated_at = now()
        projection.removed = False


def feed_window(lower, upper):
    """Preserve the published inclusive date window while excluding old seasons in SQL."""
    end = (date.fromisoformat(upper) + timedelta(days=1)).isoformat()
    return or_(
        and_(Event.starts_at >= lower, Event.starts_at < end),
        and_(
            or_(Event.starts_at.is_(None), Event.starts_at == ""),
            Event.local_date >= lower,
            Event.local_date < end,
        ),
    )


def source_candidates(db, config, existing):
    """Conservative SQL candidates; the Python inclusion/history rules stay final.

    JSON membership is structural, never a substring of a name or serialized ID.
    No personal query results are shared between users.
    """
    keys = list({x["source_key"] for x in config.get("follows", [])})
    explicit = [x["event_key"] for x in config.get("event_overrides", []) if x["state"] == "include"]
    predicates = [Event.source_key.in_(explicit)] if explicit else []
    if keys:
        predicates.extend([Event.source_key.in_(keys), Event.competition_id.in_(keys)])
        dialect = db.get_bind().dialect.name
        if dialect == "sqlite":
            members = func.json_each(Event.participants).table_valued("value")
            predicates.append(
                select(1)
                .select_from(members)
                .where(func.json_extract(members.c.value, "$.id").in_(keys))
                .correlate(Event)
                .exists()
            )
        elif dialect == "mysql":
            predicates.extend(
                func.json_contains(Event.participants, json.dumps({"id": key})) == 1 for key in keys
            )
        else:
            raise ValueError("UNSUPPORTED_DATABASE")
    if existing:
        # The passed projection set belongs to one feed. A subquery avoids an
        # unbounded IN parameter list for retained history.
        feed_id = next(iter(existing.values())).feed_id
        predicates.append(
            Event.id.in_(
                select(Projection.event_id).where(
                    Projection.feed_id == feed_id, Projection.removed.is_(False)
                )
            )
        )
    return or_(*predicates) if predicates else false()


def select_feed_events(db, config, existing, instant=None):
    instant = instant or datetime.now(timezone.utc)
    lower = (instant - timedelta(days=90)).date().isoformat()
    upper = (instant + timedelta(days=180)).date().isoformat()
    candidates = db.scalars(
        select(Event).where(feed_window(lower, upper), source_candidates(db, config, existing))
    )
    return select_candidates(candidates, config, existing, instant)


def rebuild_feed(db, owner_id: str):
    # Acquire the owner's write lock before taking the configuration snapshot.
    # A no-op UPDATE also serializes local SQLite, where FOR UPDATE is ignored.
    db.execute(update(User).where(User.id == owner_id).values(revision=User.revision))
    user = db.scalar(
        select(User).where(User.id == owner_id).with_for_update().execution_options(populate_existing=True)
    )
    feed = db.scalar(select(Feed).where(Feed.owner_id == owner_id).with_for_update())
    if not user or user.deleted or not feed or feed.paused or feed.revoked:
        return
    config = user.config
    existing = {p.event_id: p for p in db.scalars(select(Projection).where(Projection.feed_id == feed.id))}
    events, lower, _ = select_feed_events(db, config, existing)
    links, broadcasts = load_links(db, events, user)
    wanted = set()
    for event in events:
        wanted.add(event.id)
        data = projection_data(db, event, user, config, link_rows=links[event.id], broadcasts=broadcasts)
        update_projection(db, feed, event, data, existing)
    publish_snapshot(db, feed, existing, wanted, lower)


def publish_snapshot(db, feed, existing, wanted, lower, calendar_name="Anke Sports"):
    for event_id, projection in existing.items():
        if event_id not in wanted and not projection.removed:
            projection.removed = True
            projection.version += 1
            projection.updated_at = now()
    db.flush()
    keep = [p for p in existing.values() if not p.removed or p.updated_at[:10] >= lower]
    body = serialize(keep, calendar_name)
    etag = digest(body.decode())
    if feed.etag != etag:
        feed.body = body.decode()
        feed.etag = etag
        feed.revision += 1
        feed.updated_at = now()
