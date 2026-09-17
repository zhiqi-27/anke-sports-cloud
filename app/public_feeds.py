"""Account-independent, published source calendars. Anonymous reads never build work."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, update

from app.calendar import (
    event_keys,
    feed_window,
    load_links,
    projection_data,
    publish_snapshot,
    update_projection,
)
from app.config import settings
from app.db import Event, Job, Projection, PublicFeed, Source, get_db, now
from app.feed_delivery import calendar_response
from app.schemas import Config, PublicFeedView
from app.security import digest, problem
from app.service import enqueue

router = APIRouter()


def allowed(source):
    config = settings()
    return config.env == "local" or (not source.demo and source.id in config.public_feed_source_keys)


def enqueue_public_feeds(db, source_keys=None, *, force=False):
    """Called in the same transaction as schedule/broadcast changes; daily otherwise."""
    today = now()[:10]
    query = select(Source).order_by(Source.id)
    if source_keys is not None:
        query = query.where(Source.id.in_(source_keys))
    for source in db.scalars(query):
        if not allowed(source):
            continue
        ident = "public_" + digest(source.id)[:48]
        row = db.get(PublicFeed, ident)
        if not row:
            row = PublicFeed(id=ident, source_id=source.id)
            db.add(row)
            db.flush()
        # Serialize creation/scheduling with publication, including local SQLite.
        db.execute(update(PublicFeed).where(PublicFeed.id == ident).values(revision=PublicFeed.revision))
        db.refresh(row)
        if force or row.scheduled_on != today:
            enqueue(db, "public_projection", {"feed_id": ident})
            row.scheduled_on = today
    db.flush()


def schedule_public_feeds():
    from app.db import SessionLocal

    with SessionLocal() as db:
        enqueue_public_feeds(db)
        db.commit()


def rebuild_public_feed(db, ident):
    db.execute(update(PublicFeed).where(PublicFeed.id == ident).values(revision=PublicFeed.revision))
    feed = db.scalar(
        select(PublicFeed)
        .where(PublicFeed.id == ident)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not feed:
        return
    source = db.get(Source, feed.source_id)
    if not source or not allowed(source):
        return
    existing = {p.event_id: p for p in db.scalars(select(Projection).where(Projection.feed_id == ident))}
    lower = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
    upper = (datetime.now(timezone.utc) + timedelta(days=180)).date().isoformat()
    config = Config().model_dump()
    wanted = set()
    events = []
    for event in db.scalars(select(Event).where(Event.demo.is_(source.demo), feed_window(lower, upper))):
        day = (event.starts_at or event.local_date or "")[:10]
        if source.id not in event_keys(event) or not lower <= day <= upper:
            continue
        events.append(event)
    links, broadcasts = load_links(db, events, None)
    for event in events:
        wanted.add(event.id)
        # Never pass an actor: personal links, blocks, regions and pins stay private.
        data = projection_data(db, event, None, config, link_rows=links[event.id], broadcasts=broadcasts)
        update_projection(db, feed, event, data, existing)
    name = f"{'[演示] ' if source.demo else ''}Anke Sports · {source.name}"
    publish_snapshot(db, feed, existing, wanted, lower, name)


def source_feed_view(db, source_key):
    source = db.get(Source, source_key)
    if not source:
        problem("SOURCE_NOT_FOUND", "未找到球队或赛事", 404)
    feed = db.scalar(select(PublicFeed).where(PublicFeed.source_id == source.id))
    available = allowed(source)
    pending, failed = False, False
    if feed and available:
        pending = (
            db.scalar(
                select(Job.id)
                .where(
                    Job.kind == "public_projection",
                    Job.state.in_(["pending", "running"]),
                    Job.payload["feed_id"].as_string() == feed.id,
                )
                .limit(1)
            )
            is not None
        )
        outcome = db.scalar(
            select(Job)
            .where(
                Job.kind == "public_projection",
                Job.state.in_(["done", "failed"]),
                Job.payload["feed_id"].as_string() == feed.id,
            )
            .order_by(func.coalesce(Job.finished_at, Job.created_at).desc(), Job.id.desc())
            .limit(1)
        )
        failed = bool(outcome and outcome.state == "failed")
    published = bool(available and feed and feed.body)
    return {
        "source_id": source.id,
        "name": source.name,
        "demo": source.demo,
        "status": "unavailable"
        if not available
        else "updating"
        if pending
        else "error"
        if failed
        else "published"
        if published
        else "pending",
        "url": f"{settings().public_url}/public-feeds/{feed.id}.ics" if published else None,
        "revision": feed.revision if published else 0,
        "updated_at": feed.updated_at if published else None,
        "event_count": db.scalar(
            select(func.count())
            .select_from(Projection)
            .where(Projection.feed_id == feed.id, Projection.removed.is_(False))
        )
        if published
        else 0,
        "local_only": settings().env == "local",
    }


@router.get("/api/v1/public-feed", response_model=PublicFeedView)
def public_feed_info(source_key: str = Query(min_length=1, max_length=160), db=Depends(get_db)):
    return source_feed_view(db, source_key)


@router.api_route("/public-feeds/{ident}.ics", methods=["GET", "HEAD"], include_in_schema=False)
def public_feed(ident: str, request: Request, db=Depends(get_db)):
    feed = db.get(PublicFeed, ident)
    source = db.get(Source, feed.source_id) if feed else None
    if not feed or not source or not allowed(source):
        problem("PUBLIC_FEED_NOT_FOUND", "未找到可订阅的公共日历", 404)
    return calendar_response(feed, request, public=True)
