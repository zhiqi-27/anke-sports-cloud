"""Maintainer-reviewed public broadcast records and publication through the outbox."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.calendar import event_keys
from app.config import settings
from app.db import BroadcastAudit, BroadcastRecord, Event, Job, Link, Projection, User, now
from app.platforms import candidate_url, head_probe
from app.security import digest, problem
from app.service import enqueue

from app.broadcast_rules import normalized, public_metadata as public_metadata, validate_rights

BROADCAST_BATCH = 100


def utc_time(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def lock_record(db, ident):
    db.execute(
        update(BroadcastRecord)
        .where(BroadcastRecord.link_id == ident)
        .values(revision=BroadcastRecord.revision)
    )
    return db.scalar(
        select(BroadcastRecord)
        .where(BroadcastRecord.link_id == ident)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def enqueue_check(db, record):
    """Caller holds the record lock; manual and automatic checks share this decision."""
    url_hash = digest(record.published["url"])
    pending = db.scalar(
        select(Job.id)
        .where(
            Job.kind == "broadcast_check",
            Job.state.in_(["pending", "running"]),
            Job.payload["link_id"].as_string() == record.link_id,
            Job.payload["url_hash"].as_string() == url_hash,
        )
        .limit(1)
        .with_for_update()
    )
    if pending:
        return False
    enqueue(db, "broadcast_check", {"link_id": record.link_id, "url_hash": url_hash})
    return True


def inspection_deadline(db, record, instant, hours):
    deadline = instant + timedelta(hours=hours)
    starts_at = db.scalar(
        select(Event.starts_at).where(Event.id == db.get(Link, record.link_id).event_id).with_for_update()
    )
    if starts_at:
        start = utc_time(starts_at)
        if start > instant:
            deadline = min(deadline, max(start - timedelta(hours=24), instant + timedelta(hours=1)))
    return deadline.isoformat()


def audit(db, record, actor_id, action, detail):
    db.add(
        BroadcastAudit(
            link_id=record.link_id, actor_id=actor_id, action=action, revision=record.revision, detail=detail
        )
    )


def changed(db, record, expected):
    result = db.execute(
        update(BroadcastRecord)
        .where(BroadcastRecord.link_id == record.link_id, BroadcastRecord.revision == expected)
        .values(revision=expected + 1, updated_at=now())
    )
    if not result.rowcount:
        problem("REVISION_CONFLICT", "记录已被其他操作更新，请重新读取后确认", 409)
    db.refresh(record)


def record_for(db, ident):
    row = db.get(BroadcastRecord, ident)
    if not row:
        problem("BROADCAST_NOT_FOUND", "未找到直播审核记录", 404)
    return row


def publish_event_change(db, event_id):
    event = db.get(Event, event_id)
    event.updated_at = now()
    from app.public_feeds import enqueue_public_feeds

    enqueue_public_feeds(db, event_keys(event), force=True)
    previous = set(db.scalars(select(Projection.feed_id).where(Projection.event_id == event_id)))
    from app.db import Feed

    historical_owners = (
        set(db.scalars(select(Feed.owner_id).where(Feed.id.in_(previous)))) if previous else set()
    )
    for user in db.scalars(select(User).where(User.deleted.is_(False))):
        follows = {x["source_key"] for x in user.config.get("follows", [])}
        manual = {x["event_id"] for x in user.config.get("manual_events", [])}
        if user.id in historical_owners or event_keys(event) & follows or event.id in manual:
            enqueue(db, "projection", {"user_id": user.id})


def create_record(db, actor_id, data):
    value = normalized(data)
    event = db.get(Event, value["event_id"])
    if not event:
        problem("EVENT_NOT_FOUND", "请选择已存在的比赛", 404)
    if db.scalar(
        select(Link.id).where(
            Link.owner_id == "public", Link.event_id == event.id, Link.url_hash == digest(value["url"])
        )
    ):
        problem("BROADCAST_EXISTS", "本场已有这个公共链接，请编辑已有记录", 409)
    _, platform = candidate_url(value["url"])
    link = Link(
        owner_id="public",
        event_id=event.id,
        url=value["url"],
        url_hash=digest(value["url"]),
        title=value["title"],
        kind="watch_along" if value["content_type"] == "watch_along" else "live",
        platform=platform,
        origin="official",
        available=False,
    )
    db.add(link)
    db.flush()
    record = BroadcastRecord(link_id=link.id, draft=value)
    db.add(record)
    db.flush()
    audit(db, record, actor_id, "draft_created", {"draft": value})
    return record


def edit_record(db, actor_id, ident, data, expected):
    record = record_for(db, ident)
    value = normalized(data)
    if value["event_id"] != db.get(Link, ident).event_id:
        problem("EVENT_IMMUTABLE", "本记录绑定的比赛不可修改，请为其他比赛新建记录")
    changed(db, record, expected)
    record.draft = value
    audit(db, record, actor_id, "draft_edited", {"draft": value})
    return record


def approve_record(db, actor_id, ident, data):
    record = record_for(db, ident)
    instant = datetime.now(timezone.utc)
    if not data.source_and_event_confirmed:
        problem("EVIDENCE_REQUIRED", "必须先核对官方来源、具体场次与链接类型")
    if not instant < data.valid_until <= instant + timedelta(days=7):
        problem("INVALID_VALIDITY", "请设置未来7天内的复查期限")
    value = normalized(record.draft)
    link = db.get(Link, ident)
    event = db.get(Event, link.event_id)
    validate_rights(value, event.competition_id)
    duplicate = db.scalar(
        select(Link.id).where(
            Link.owner_id == "public",
            Link.event_id == link.event_id,
            Link.url_hash == digest(value["url"]),
            Link.id != ident,
        )
    )
    if duplicate:
        problem("BROADCAST_EXISTS", "这个链接已由本场另一条记录维护", 409)
    changed(db, record, data.expected_revision)
    previous = record.published
    record.published = {
        **value,
        "reviewed_at": instant.isoformat(),
        "valid_until": data.valid_until.astimezone(timezone.utc).isoformat(),
    }
    record.expires_at = record.published["valid_until"]
    record.published_revision = record.revision
    record.status = "published"
    if not previous or previous["url"] != value["url"] or previous["content_type"] != value["content_type"]:
        record.device_tests = []
        record.network_status, record.network_checked_at = "not_checked", None
    record.missing_count = 0
    record.next_check_at = now()
    link.url, link.url_hash, link.title = value["url"], digest(value["url"]), value["title"]
    link.kind = "watch_along" if value["content_type"] == "watch_along" else "live"
    link.platform = candidate_url(value["url"])[1]
    link.access = value["access"]
    link.regions = value["regions"] if value["region_mode"] == "include" else []
    link.available = True
    audit(db, record, actor_id, "published", {"publication": record.published})
    publish_event_change(db, link.event_id)
    return record


def suspend_record(db, actor_id, ident, expected, reason, status="suspended"):
    record = record_for(db, ident)
    changed(db, record, expected)
    record.status = status
    link = db.get(Link, ident)
    link.available = False
    audit(db, record, actor_id, status, {"reason": reason})
    publish_event_change(db, link.event_id)
    return record


def add_device_evidence(db, actor_id, ident, data):
    record = record_for(db, ident)
    if record.status != "published" or not record.published:
        problem("PUBLICATION_REQUIRED", "请先审核发布当前链接，再记录针对该版本的设备观察")
    changed(db, record, data.expected_revision)
    evidence = data.model_dump(mode="json", exclude={"expected_revision"})
    evidence["url_hash"] = digest(record.published["url"])
    record.device_tests = [*record.device_tests[-19:], evidence]
    audit(db, record, actor_id, "device_evidence", {"observation": evidence})
    db.get(Event, db.get(Link, ident).event_id).updated_at = now()
    return record


def queue_check(db, actor_id, ident, expected):
    record = record_for(db, ident)
    if record.status != "published" or not record.published:
        problem("PUBLICATION_REQUIRED", "请先发布待检查的链接")
    changed(db, record, expected)
    enqueue_check(db, record)
    audit(db, record, actor_id, "check_requested", {})
    return record


def check_record(db, ident, url_hash):
    record = record_for(db, ident)
    if record.status != "published" or not record.published or digest(record.published["url"]) != url_hash:
        return
    instant = datetime.now(timezone.utc)
    if utc_time(record.published["valid_until"]) <= instant:
        suspend_record(db, "worker", ident, record.revision, "审核期限已到，请重新核对官方入口", "expired")
        return
    outcome = head_probe(record.published["url"])
    checked_before = record.network_checked_at
    previous_outcome = record.network_status
    changed(db, record, record.revision)
    record.network_status, record.network_checked_at = outcome, instant.isoformat()
    record.next_check_at = inspection_deadline(
        db, record, instant, 1 if outcome in {"retry", "not_found"} else 6
    )
    if outcome == "not_found":
        if not checked_before or instant - datetime.fromisoformat(checked_before) >= timedelta(minutes=5):
            record.missing_count += 1
    elif outcome == "reachable":
        record.missing_count = 0
    audit(db, record, "worker", "network_checked", {"outcome": outcome, "http_only": True})
    if outcome in {"unsafe", "redirect_review"} or record.missing_count >= 2:
        suspend_record(
            db,
            "worker",
            ident,
            record.revision,
            "链接需人工重新核对",
            "needs_review" if outcome != "not_found" else "unavailable",
        )
    elif previous_outcome != outcome:
        # Public network status changes do not imply changed event content or a new ICS version.
        db.get(Event, db.get(Link, ident).event_id).updated_at = now()


def schedule_broadcasts():
    from app.db import SessionLocal

    instant = now()
    counts = {"examined": 0, "normalized": 0, "expired": 0, "queued": 0}
    # IDs only, in index order. Old rows have an empty expiry and are normalized
    # in these same bounded transactions. Network checks remain independently optional.
    with SessionLocal() as db:
        expiring = db.scalars(
            select(BroadcastRecord.link_id)
            .where(BroadcastRecord.status == "published", BroadcastRecord.expires_at <= instant)
            .order_by(BroadcastRecord.expires_at, BroadcastRecord.link_id)
            .limit(BROADCAST_BATCH)
        ).all()
    for ident in expiring:
        with SessionLocal() as db:
            record = lock_record(db, ident)
            if not record or record.status != "published":
                continue
            counts["examined"] += 1
            record.expires_at = utc_time(record.published["valid_until"]).isoformat()
            if record.expires_at <= instant:
                suspend_record(
                    db,
                    "worker",
                    record.link_id,
                    record.revision,
                    "审核期限已到，请重新核对官方入口",
                    "expired",
                )
                counts["expired"] += 1
            else:
                counts["normalized"] += 1
            db.commit()
    if not settings().broadcast_checks_enabled:
        return counts
    with SessionLocal() as db:
        pending = (
            select(Job.id)
            .where(
                Job.kind == "broadcast_check",
                Job.state.in_(["pending", "running"]),
                Job.payload["link_id"].as_string() == BroadcastRecord.link_id,
                Job.payload["url_hash"].as_string() == Link.url_hash,
            )
            .exists()
        )
        due = db.scalars(
            select(BroadcastRecord.link_id)
            .join(Link, Link.id == BroadcastRecord.link_id)
            .where(
                BroadcastRecord.status == "published",
                BroadcastRecord.next_check_at <= instant,
                ~pending,
            )
            .order_by(BroadcastRecord.next_check_at, BroadcastRecord.link_id)
            .limit(BROADCAST_BATCH)
        ).all()
    for ident in due:
        with SessionLocal() as db:
            record = lock_record(db, ident)
            if not record or record.status != "published" or record.next_check_at > instant:
                continue
            counts["examined"] += 1
            if utc_time(record.published["valid_until"]) <= utc_time(instant):
                suspend_record(
                    db, "worker", ident, record.revision, "审核期限已到，请重新核对官方入口", "expired"
                )
                counts["expired"] += 1
            elif enqueue_check(db, record):
                record.next_check_at = (utc_time(instant) + timedelta(hours=1)).isoformat()
                counts["queued"] += 1
            db.commit()
    return counts


def record_view(db, record):
    link = db.get(Link, record.link_id)
    event = db.get(Event, link.event_id)
    return {
        "id": record.link_id,
        "revision": record.revision,
        "status": record.status,
        "draft": record.draft,
        "published": record.published,
        "published_revision": record.published_revision,
        "draft_changed": not record.published
        or any(record.published.get(k) != v for k, v in record.draft.items()),
        "event_title": event.title,
        "event_demo": event.demo,
        "network_status": record.network_status,
        "network_checked_at": record.network_checked_at,
        "next_check_at": record.next_check_at,
        "device_tests": record.device_tests,
        "audit": [
            {"action": a.action, "revision": a.revision, "detail": a.detail, "created_at": a.created_at}
            for a in db.scalars(
                select(BroadcastAudit)
                .where(BroadcastAudit.link_id == record.link_id)
                .order_by(BroadcastAudit.created_at.desc(), BroadcastAudit.id.desc())
                .limit(30)
            )
        ],
    }
