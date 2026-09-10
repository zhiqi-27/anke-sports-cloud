"""Preview follow changes using the same selection rules as published personal calendars."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.calendar import select_feed_events
from app.follow_rules import follow_impact
from app.db import Event, Feed, Job, Projection, Source
from app.security import problem


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
    return follow_impact(
        user,
        config,
        feed,
        existing,
        selected,
        lower,
        upper,
        instant,
        source_for_key=lambda key: db.get(Source, key),
        event_for_key=lambda key: db.scalar(select(Event).where(Event.source_key == key)),
        event_by_id=lambda ident: db.get(Event, ident),
        all_events=db.scalars(select(Event)),
        publication_pending=db.scalar(
            select(Job.id)
            .where(
                Job.kind == "projection",
                Job.state.in_(["pending", "running"]),
                Job.payload["user_id"].as_string() == user.id,
            )
            .limit(1)
        )
        is not None,
    )
