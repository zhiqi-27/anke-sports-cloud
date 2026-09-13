import os
from datetime import datetime, timedelta

import httpx as httpx  # Preserve the injectable HTTP transport used by integration tests.
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.db import BroadcastRecord, Event, Job, Link, ProviderState, Source, now
from app.config import settings

PROVIDER_REFRESH = timedelta(hours=6)


def provider_due(instant):
    cutoff = (datetime.fromisoformat(instant) - PROVIDER_REFRESH).isoformat()
    return (
        ProviderState.enabled.is_(True),
        or_(ProviderState.last_success.is_(None), ProviderState.last_success <= cutoff),
        or_(ProviderState.last_attempt_at.is_(None), ProviderState.last_attempt_at <= cutoff),
        or_(ProviderState.next_attempt_at.is_(None), ProviderState.next_attempt_at <= instant),
    )


def enqueue_provider(db, provider, *, scheduled=False, instant=None):
    from app.service import enqueue

    instant = instant or now()
    if not db.get(ProviderState, provider):
        try:
            with db.begin_nested():
                db.add(ProviderState(id=provider))
                db.flush()
        except IntegrityError:
            pass  # Another first-use request inserted the same provider.
    # Lock before the decision; CURRENT reads also avoid a stale MySQL snapshot.
    db.execute(update(ProviderState).where(ProviderState.id == provider).values(error=ProviderState.error))
    row = db.scalar(
        select(ProviderState)
        .where(ProviderState.id == provider, *(provider_due(instant) if scheduled else ()))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not row:
        return False
    pending = db.scalar(
        select(Job.id)
        .where(
            Job.kind == "provider",
            Job.state.in_(["pending", "running"]),
            Job.payload["provider"].as_string() == provider,
        )
        .limit(1)
        .with_for_update()
    )
    if pending:
        return False
    enqueue(db, "provider", {"provider": provider})
    return True


def get_json(client, path, **kwargs):
    result = client.get(path, **kwargs)
    result.raise_for_status()
    return result.json()


def provider_key(name):
    # Explicit process configuration wins, including an empty value to disable it.
    value = os.getenv(name)
    if value is not None:
        return value
    field = {
        "BALLDONTLIE_API_KEY": "balldontlie_api_key",
        "FOOTBALL_DATA_API_KEY": "football_data_api_key",
        "YOUTUBE_API_KEY": "youtube_api_key",
    }[name]
    return getattr(settings(), field).get_secret_value()


def provider_statuses(db):
    instant = now()
    provider = Job.payload["provider"].as_string()
    activities = dict(
        db.execute(
            select(
                provider,
                func.max(
                    case(
                        (and_(Job.state == "running", Job.due_at > instant), 3),
                        (Job.due_at <= instant, 2),
                        else_=1,
                    )
                ),
            )
            .where(Job.kind == "provider", Job.state.in_(["pending", "running"]))
            .group_by(provider)
        ).all()
    )
    labels = {0: "idle", 1: "waiting", 2: "queued", 3: "running"}
    return [
        {
            "id": p.id,
            "last_success": p.last_success,
            "error": p.error,
            "enabled": p.enabled,
            "consecutive_failures": p.consecutive_failures,
            "next_attempt_at": p.next_attempt_at,
            "activity": labels[activities.get(p.id, 0)],
        }
        for p in db.scalars(select(ProviderState))
    ]


def source(db, key, name, short, sport, kind, provider, color="#8bbdaa", logo_url=None):
    obj = db.get(Source, key)
    if not obj:
        obj = Source(
            id=key,
            name=name,
            short_name=short,
            sport=sport,
            kind=kind,
            provider=provider,
            color=color,
            logo_url=logo_url,
            demo=False,
        )
        db.add(obj)
    elif obj.logo_url != logo_url:
        obj.logo_url = logo_url
    return {"id": key, "name": name, "short_name": short, "color": color, "logo_url": logo_url}


def upsert_event(db, key, **values):
    event = db.scalar(select(Event).where(Event.source_key == key))
    if event is None:
        event = Event(source_key=key, **values)
        db.add(event)
    elif any(getattr(event, k) != v for k, v in values.items()):
        rescheduled = "starts_at" in values and event.starts_at != values["starts_at"]
        for k, value in values.items():
            setattr(event, k, value)
        event.updated_at = now()
        if rescheduled:
            # An earlier start can enter the hourly inspection window immediately.
            # Only scheduling changes here; existing review/network evidence stays intact.
            db.execute(
                update(BroadcastRecord)
                .where(
                    BroadcastRecord.status == "published",
                    BroadcastRecord.link_id.in_(select(Link.id).where(Link.event_id == event.id)),
                )
                .values(next_check_at=now())
            )


def sync_provider(db, provider):
    from app.provider_adapters import fetch_schedule

    events, sources = fetch_schedule(provider, request_json=get_json, key_reader=provider_key)
    for row in sources:
        source(
            db,
            row["id"],
            row["name"],
            row["short_name"],
            row["sport"],
            row["kind"],
            provider,
            row["color"],
            row.get("logo_url"),
        )
    for row in events:
        upsert_event(
            db, row["source_key"], **{key: value for key, value in row.items() if key != "source_key"}
        )
    state = db.get(ProviderState, provider)
    if not state:
        state = ProviderState(id=provider)
        db.add(state)
    state.last_success, state.error, state.enabled = now(), "", True
    from app.public_feeds import enqueue_public_feeds

    enqueue_public_feeds(
        db, list(db.scalars(select(Source.id).where(Source.provider == provider))), force=True
    )


def youtube_request(endpoint, params):
    from app import youtube_budget
    from app.youtube_transport import request

    return request(endpoint, params, key=provider_key("YOUTUBE_API_KEY"), budget=youtube_budget)


def resolve_creator(value):
    from app.youtube_transport import resolve_creator as resolve

    return resolve(value, youtube_request)
