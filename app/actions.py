"""Transport-independent calendar queries and commands shared by HTTP and MCP."""

import json
import re
import time

from sqlalchemy import and_, or_, select

from app.calendar import event_view, load_links
from app.schedule_rules import schedule_page, schedule_range
from app.config import settings
from app.db import CommandReceipt, Event, Feed, Link, Source, User
from app.schemas import Config
from app.security import digest, problem
from app.service import active_user, attach_link, import_preview, save_config, user_view
from app.source_rules import source_logo_url


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
                **(
                    {"logo_url": logo}
                    if (logo := source_logo_url(s.id, s.short_name, s.logo_url))
                    else {}
                ),
                "demo": s.demo,
            }
            for s in rows
            if q.casefold() in (s.name + s.short_name).casefold()
        ]
    }


def get_schedule(
    db,
    from_,
    to,
    dataset="real",
    followed=False,
    q="",
    user=None,
    limit=500,
    cursor=None,
    source_id="",
):
    lower, upper, earliest, latest = schedule_range(from_, to, dataset, q, limit)
    if followed and not user:
        problem("AUTH_REQUIRED", "登录后查看个人赛程", 401)
    columns = [Event.id, Event.starts_at, Event.local_date, Event.updated_at]
    if followed or source_id:
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
    page, next_cursor = schedule_page(
        db.execute(query),
        from_,
        to,
        dataset,
        followed,
        q,
        user,
        limit,
        cursor,
        source_id,
    )
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


def command(db, user, operation, key, payload, perform, *, prepare=None):
    """Persist a retry receipt in the same transaction as the mutation and outbox.

    MySQL serializes per-owner commands. Config CAS remains active for all
    transports. Clients retain a key after uncertain responses, for 24 hours.
    """
    prepared, did_prepare = None, False
    if prepare:
        # Network preparation cannot hold an owner write lock: request budget
        # reservations commit independently before HTTP. Recheck identity and
        # receipt under the owner lock afterwards, before any business mutation.
        if not db.scalar(select(User.id).where(User.id == user.id, User.deleted.is_(False))):
            problem("ACCOUNT_DELETED", "账号已删除", 403)
        if key is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", key):
            problem("INVALID_IDEMPOTENCY_KEY", "幂等键需要 8 至 128 个字母、数字或 ._:-")
        cached = db.get(CommandReceipt, digest(user.id + ":" + key)) if key else None
        if not cached or cached.expires_at <= int(time.time()):
            prepared, did_prepare = prepare(), True

    def invoke():
        if prepare:
            if not did_prepare:
                problem("COMMAND_RETRY", "命令回执已变化，请使用原幂等键重试", 409)
            return perform(prepared)
        return perform()

    active_user(db, user.id)
    if key is None:
        return invoke()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", key):
        problem("INVALID_IDEMPOTENCY_KEY", "幂等键需要 8 至 128 个字母、数字或 ._:-")
    receipt_id = digest(user.id + ":" + key)
    fingerprint = digest(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    prior = db.scalar(
        select(CommandReceipt)
        .where(CommandReceipt.id == receipt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if prior and prior.expires_at > int(time.time()):
        if prior.operation != operation or prior.payload_hash != fingerprint:
            problem("IDEMPOTENCY_CONFLICT", "此幂等键已用于不同操作", 409)
        return prior.result
    if prior:
        db.delete(prior)
        db.flush()
    result = invoke()
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


def add_creator(db, user, data, details):
    from app.content import save_creator

    save_creator(db, user, details, data.scope_keys, data.preview, data.recap, True, data.expected_revision)
    return user_view(db, user)


def import_config(db, user, data):
    if data.expected_revision != user.revision:
        problem("REVISION_CONFLICT", "配置已更新，请重新预览", 409)
    config, preview = import_preview(db, user, data)
    if not data.dry_run:
        from app.config_rules import confirm_import

        confirm_import(data, preview, settings().cipher())
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
