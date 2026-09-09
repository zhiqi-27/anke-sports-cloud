"""Preview follow changes using the same selection rules as published personal calendars."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select

from app.calendar import event_is_past, event_keys, included, select_feed_events
from app.db import Event, Feed, Job, Projection, Source
from app.security import digest, problem


def proposed_config(db, user, data):
    if data.expected_revision != user.revision:
        problem("REVISION_CONFLICT", "配置已在其他页面更新，请刷新后重试", 409)
    for follow in data.follows:
        source = db.get(Source, follow.source_key)
        event = (
            db.scalar(select(Event).where(Event.source_key == follow.source_key))
            if follow.type == "event"
            else None
        )
        if not (source and source.kind == follow.type) and not event:
            problem("SOURCE_NOT_FOUND", "该关注对象或类型尚未接入")
    follows = {f.source_key: f.model_dump() for f in data.follows}
    return {**user.config, "follows": [follows[key] for key in sorted(follows)]}


def preview_follows(db, user, data):
    config = proposed_config(db, user, data)
    feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
    existing = {p.event_id: p for p in db.scalars(select(Projection).where(Projection.feed_id == feed.id))}
    instant = datetime.now(timezone.utc)
    selected, lower, upper = select_feed_events(db, config, existing, instant)
    after = {event.id: event for event in selected}
    before = {key for key, row in existing.items() if not row.removed}
    previous_follows = {f["source_key"]: f for f in user.config["follows"]}
    new_follows = {f["source_key"]: f for f in config["follows"]}
    dropped = previous_follows.keys() - new_follows.keys()

    def source_summary(value):
        source = db.get(Source, value["source_key"])
        event = (
            db.scalar(select(Event).where(Event.source_key == value["source_key"]))
            if value["type"] == "event"
            else None
        )
        return {
            **value,
            "name": source.name if source else event.title if event else value["source_key"],
            "demo": source.demo if source else event.demo if event else None,
        }

    def event_summary(ident):
        event = after.get(ident) or db.get(Event, ident)
        value = event or SimpleNamespace(**existing[ident].data, timezone="UTC")
        return {
            "id": ident,
            "title": value.title,
            "starts_at": value.starts_at,
            "local_date": value.local_date,
            "time_precision": value.time_precision,
            "past": event_is_past(value, instant),
            "demo": event.demo if event else None,
        }

    def group(ids):
        rows = sorted(
            (event_summary(ident) for ident in ids),
            key=lambda e: (e["starts_at"] or e["local_date"] or "9999", e["id"]),
        )
        past = sum(row["past"] for row in rows)
        return {"total": len(rows), "future": len(rows) - past, "past": past, "items": rows}

    retained = {
        ident
        for ident in before & after.keys()
        if event_keys(after[ident]) & dropped
        and included(after[ident], config)
        and not event_is_past(after[ident], instant)
    }
    result = {
        "revision": user.revision,
        "added_sources": [
            source_summary(new_follows[key]) for key in sorted(new_follows.keys() - previous_follows.keys())
        ],
        "removed_sources": [source_summary(previous_follows[key]) for key in sorted(dropped)],
        "added": group(after.keys() - before),
        "removed": group(before - after.keys()),
        "retained": group(retained),
        "historical_retained": sum(
            ident in before and event_is_past(event, instant) and not included(event, config)
            for ident, event in after.items()
        ),
        "result_count": len(after),
        "undated_count": sum(
            not event.starts_at and not event.local_date and included(event, config)
            for event in db.scalars(select(Event))
        ),
        "window_start": lower,
        "window_end": upper,
        "feed_paused": feed.paused,
        "publication_pending": db.scalar(
            select(Job.id)
            .where(
                Job.kind == "projection",
                Job.state.in_(["pending", "running"]),
                Job.payload["user_id"].as_string() == user.id,
            )
            .limit(1)
        )
        is not None,
    }
    # Staleness check, not authorization: actor/scopes and revision checks remain authoritative.
    # Hash full affected membership before limiting display examples; no secrets in the result.
    result["confirmation"] = digest(
        json.dumps(
            {"owner": user.id, "follows": config["follows"], "preview": result},
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    for key in ("added", "removed", "retained"):
        result[key]["items"] = result[key]["items"][:10]
    return result
