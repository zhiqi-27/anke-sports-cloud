"""Follow impact semantics shared by SQL and document transports."""

import json
from types import SimpleNamespace

from app.calendar_rules import event_is_past, event_keys, included
from app.security import digest
from app.source_rules import source_is_selectable


def direct_follow_allowed(source):
    return source.kind == "team" and source_is_selectable(source)


def follow_impact(
    user,
    config,
    feed,
    existing,
    selected,
    lower,
    upper,
    instant,
    *,
    source_for_key,
    event_by_id,
    all_events,
    publication_pending,
):
    after = {event.id: event for event in selected}
    before = {key for key, row in existing.items() if not row.removed}
    previous_follows = {f["source_key"]: f for f in user.config["follows"]}
    new_follows = {f["source_key"]: f for f in config["follows"]}
    dropped = previous_follows.keys() - new_follows.keys()

    def source_summary(value):
        source = source_for_key(value["source_key"])
        return {
            **value,
            "name": source.name if source else value["source_key"],
            "demo": source.demo if source else None,
        }

    def event_summary(ident):
        event = after.get(ident) or event_by_id(ident)
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
            not event.starts_at and not event.local_date and included(event, config) for event in all_events
        ),
        "window_start": lower,
        "window_end": upper,
        "feed_paused": feed.paused,
        "publication_pending": publication_pending,
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
