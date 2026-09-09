"""Transport-independent calendar queries and commands shared by HTTP and MCP."""

import base64
import json
import re
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from app.calendar import event_view, inclusion_filter, load_links
from app.config import settings
from app.db import CommandReceipt, Event, Feed, Link, Source, User
from app.schemas import Config
from app.security import digest, problem
from app.service import attach_link, import_preview, save_config, user_view


def find_event(db, event_id):
    event = db.get(Event, event_id)
    if not event:
        problem("EVENT_NOT_FOUND", "未找到这场比赛", 404)
    return event


def search_sources(db, q="", dataset="real"):
    if dataset not in {"real", "demo"} or len(q) > 200:
        problem("INVALID_QUERY", "查询条件无效")
    rows = db.scalars(
        select(Source).where(Source.demo.is_(dataset == "demo")).order_by(Source.kind, Source.name)
    )
    return {
        "items": [
            {
                "id": s.id,
                "name": s.name,
                "short_name": s.short_name,
                "sport": s.sport,
                "kind": s.kind,
                "color": s.color,
                "demo": s.demo,
            }
            for s in rows
            if q.casefold() in (s.name + s.short_name).casefold()
        ]
    }


def get_schedule(db, from_, to, dataset="real", followed=False, q="", user=None, limit=500, cursor=None):
    if dataset not in {"real", "demo"} or len(q) > 200 or not 1 <= limit <= 500:
        problem("INVALID_QUERY", "查询条件无效")
    if followed and not user:
        problem("AUTH_REQUIRED", "登录后查看个人赛程", 401)
    try:
        lower, upper = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in (from_, to))
        if not lower.tzinfo or not upper.tzinfo or not timedelta(0) < upper - lower <= timedelta(days=180):
            raise ValueError()
        lower_utc, upper_utc = lower.astimezone(timezone.utc), upper.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        problem("INVALID_RANGE", "请查询带时区、最长 180 天的有效时间范围")
    # ISO offset strings are not ordered by absolute time. Use a conservative
    # indexed date envelope, then compare actual instants below. UTC offsets
    # are less than 24 hours; this includes both extreme offsets at the edges.
    earliest = date.fromordinal(max(date.min.toordinal(), lower_utc.date().toordinal() - 1)).isoformat()
    last_day = upper_utc.date().toordinal() + 2
    latest = date.fromordinal(last_day).isoformat() if last_day <= date.max.toordinal() else None
    columns = [Event.id, Event.starts_at, Event.local_date, Event.updated_at]
    if followed:
        columns += [Event.source_key, Event.competition_id, Event.participants]
    if q:
        columns.append(Event.title)
    query = select(*columns).where(
        Event.demo.is_(dataset == "demo"),
        or_(
            and_(Event.starts_at >= earliest, Event.starts_at < latest if latest else True),
            and_(
                or_(Event.starts_at.is_(None), Event.starts_at == ""),
                Event.local_date >= lower.date().isoformat(),
                Event.local_date < upper.date().isoformat(),
            ),
        ),
    )
    result = []
    accepts = inclusion_filter(user.config) if followed else None
    needle = q.casefold()
    for event in db.execute(query):
        if accepts is not None and not accepts(event):
            continue
        if needle and needle not in event.title.casefold():
            continue
        start = datetime.fromisoformat(event.starts_at.replace("Z", "+00:00")) if event.starts_at else None
        if start is not None and not lower <= start < upper:
            continue
        if start is None and (
            not event.local_date
            or not lower.date().isoformat() <= event.local_date < upper.date().isoformat()
        ):
            continue
        order = start.astimezone(timezone.utc).isoformat() if start else event.local_date
        result.append((order, event))
    result.sort(key=lambda item: (item[0], item[1].id))
    binding = digest(
        json.dumps(
            [
                from_,
                to,
                dataset,
                followed,
                q,
                user.id if user else None,
                user.revision if user else None,
                [(e.id, e.updated_at) for _, e in result],
            ]
        )
    )
    offset = 0
    if cursor:
        try:
            if len(cursor) > 200:
                raise ValueError()
            value = json.loads(base64.urlsafe_b64decode(cursor))
            offset = value["offset"]
            if value["binding"] != binding or type(offset) is not int or not 0 <= offset <= len(result):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            problem("CURSOR_EXPIRED", "赛程或查询已变化，请重新查询第一页", 409)
    next_cursor = (
        base64.urlsafe_b64encode(json.dumps({"offset": offset + limit, "binding": binding}).encode()).decode()
        if offset + limit < len(result)
        else None
    )
    page = [event for _, event in result[offset : offset + limit]]
    current = (
        {
            event.id: event
            for event in db.scalars(
                select(Event)
                .where(Event.id.in_([event.id for event in page]))
                .execution_options(populate_existing=True)
            )
        }
        if page
        else {}
    )
    if any(event.id not in current or current[event.id].updated_at != event.updated_at for event in page):
        problem("CURSOR_EXPIRED", "赛程或查询已变化，请重新查询第一页", 409)
    events = [current[event.id] for event in page]
    links, broadcasts = load_links(db, events, user)
    return {
        "items": [
            event_view(db, event, user, link_rows=links[event.id], broadcasts=broadcasts) for event in events
        ],
        "next_cursor": next_cursor,
        "coverage": {
            "dataset": dataset,
            "complete": dataset == "demo",
            "note": "synthetic fixtures"
            if dataset == "demo"
            else "Only connected provider snapshots; coverage is not guaranteed",
        },
    }


def command(db, user, operation, key, payload, perform):
    """Persist a retry receipt in the same transaction as the mutation and outbox.

    MySQL serializes per-owner commands. Config CAS remains active for all
    transports. Clients retain a key after uncertain responses, for 24 hours.
    """
    if key is None:
        return perform()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", key):
        problem("INVALID_IDEMPOTENCY_KEY", "幂等键需要 8 至 128 个字母、数字或 ._:-")
    db.execute(select(User.id).where(User.id == user.id).with_for_update())
    receipt_id = digest(user.id + ":" + key)
    fingerprint = digest(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    prior = db.get(CommandReceipt, receipt_id)
    if prior and prior.expires_at > int(time.time()):
        if prior.operation != operation or prior.payload_hash != fingerprint:
            problem("IDEMPOTENCY_CONFLICT", "此幂等键已用于不同操作", 409)
        return prior.result
    if prior:
        db.delete(prior)
        db.flush()
    result = perform()
    db.add(
        CommandReceipt(
            id=receipt_id,
            owner_id=user.id,
            operation=operation,
            payload_hash=fingerprint,
            result=result,
            expires_at=int(time.time()) + 86400,
        )
    )
    db.flush()
    return result


def set_follows(db, user, data):
    from app.follow_changes import preview_follows, proposed_config

    config = proposed_config(db, user, data)
    if data.confirmation and preview_follows(db, user, data)["confirmation"] != data.confirmation:
        problem("FOLLOWS_PREVIEW_CHANGED", "赛程或订阅内容已变化，请重新预览后保存", 409)
    save_config(db, user, config, data.expected_revision)
    return user_view(db, user)


def add_link(db, user, event_id, data):
    event = find_event(db, event_id)
    link = attach_link(db, user, event, data.url, data.title, data.kind)
    return {"id": link.id, "event": event_view(db, event, user)}


def block_link(db, user, link_id):
    link = db.get(Link, link_id)
    if not link or link.owner_id not in {user.id, "public"}:
        problem("NOT_FOUND", "未找到此链接", 404)
    event = find_event(db, link.event_id)
    overrides = [
        x for x in user.config["link_overrides"] if (x["event_key"], x["url"]) != (event.source_key, link.url)
    ]
    overrides.append({"event_key": event.source_key, "url": link.url, "state": "block"})
    save_config(db, user, {**user.config, "link_overrides": overrides}, user.revision)
    return {"blocked": True}


def add_creator(db, user, data):
    from app.content import save_creator
    from app.providers import resolve_creator

    details = resolve_creator(data.url.strip())
    save_creator(db, user, details, data.scope_keys, data.preview, data.recap, True, data.expected_revision)
    return user_view(db, user)


def import_config(db, user, data):
    if data.expected_revision != user.revision:
        problem("REVISION_CONFLICT", "配置已更新，请重新预览", 409)
    config, preview = import_preview(db, user, data)
    if not data.dry_run:
        try:
            valid = settings().cipher().decrypt(
                (data.confirmation or "").encode()
            ) == settings().cipher().decrypt(preview["confirmation"].encode())
        except Exception:
            valid = False
        if not valid:
            problem("PREVIEW_REQUIRED", "请先预览并确认本次导入")
        if preview["unresolved"]:
            problem("UNRESOLVED_CONFIG", "存在无法解析的对象，请修正后导入")
        save_config(db, user, config, data.expected_revision)
    return {**preview, "applied": not data.dry_run}


def feed_address(db, user):
    feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
    token = settings().cipher().decrypt(feed.token_ciphertext.encode()).decode()
    return {
        "url": f"{settings().public_url.rstrip('/')}/feeds/{token}.ics",
        "local_only": settings().env == "local",
    }


def export_config(user):
    return Config.model_validate(user.config).model_dump()
