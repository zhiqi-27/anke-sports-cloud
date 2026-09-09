import json
import secrets

from sqlalchemy import func, select, update

from app.config import settings
from app.db import Creator, Event, Feed, Job, Link, Projection, Source, User
from app.schemas import Config, ImportInput
from app.security import canonical_url, digest, problem


def ensure_user(db, user_id: str) -> User:
    user = db.get(User, user_id)
    if user and user.deleted:
        problem("ACCOUNT_DELETED", "账号已删除", 403)
    if user is None:
        user = User(
            id=user_id,
            display_name="本地体验账号" if user_id == "local-reviewer" else "Anke Sports 用户",
            config=Config().model_dump(),
            revision=0,
        )
        db.add(user)
        token = secrets.token_urlsafe(32)
        db.add(
            Feed(
                owner_id=user_id,
                token_hash=digest(token),
                token_ciphertext=settings().cipher().encrypt(token.encode()).decode(),
            )
        )
        enqueue(db, "projection", {"user_id": user_id})
        db.flush()
    return user


def lock_user(db, user_id):
    db.execute(update(User).where(User.id == user_id).values(revision=User.revision))
    return db.scalar(
        select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    )


def active_user(db, user_id):
    user = lock_user(db, user_id)
    if not user or user.deleted:
        problem("ACCOUNT_DELETED", "账号已删除", 403)
    return user


def enqueue(db, kind: str, payload: dict):
    if owner_id := payload.get("user_id"):
        user = lock_user(db, owner_id)
        if not user or user.deleted:
            return False
    db.add(Job(kind=kind, payload=payload))
    return True


def save_config(db, user: User, config: dict, revision: int):
    clean = Config.model_validate(config).model_dump()
    result = db.execute(
        update(User)
        .where(User.id == user.id, User.revision == revision, User.deleted.is_(False))
        .values(config=clean, revision=revision + 1)
    )
    if not result.rowcount:
        problem("REVISION_CONFLICT", "配置已在其他页面更新，请刷新后重试", 409)
    enqueue(db, "projection", {"user_id": user.id})
    db.flush()
    db.refresh(user)
    for creator in user.config.get("creators", []):
        if creator["enabled"]:
            enqueue(db, "youtube_rematch", {"user_id": user.id, "channel_id": creator["channel_id"]})


def user_view(db, user: User) -> dict:
    from app.content import creator_status

    feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
    pending = db.scalar(
        select(Job.id)
        .where(
            Job.kind == "projection",
            Job.state.in_(["pending", "running"]),
            Job.payload["user_id"].as_string() == user.id,
        )
        .limit(1)
    )
    outcome = db.scalar(
        select(Job)
        .where(
            Job.kind == "projection",
            Job.state.in_(["done", "failed"]),
            Job.payload["user_id"].as_string() == user.id,
        )
        .order_by(func.coalesce(Job.finished_at, Job.created_at).desc(), Job.id.desc())
        .limit(1)
    )
    failed = (
        outcome
        and outcome.state == "failed"
        and (outcome.finished_at or outcome.created_at) > feed.updated_at
    )
    creators = {c.channel_id: c for c in db.scalars(select(Creator))}
    return {
        "id": user.id,
        "display_name": user.display_name,
        "is_maintainer": user.id in settings().maintainer_ids,
        "revision": user.revision,
        "config": user.config,
        "creators": [
            {
                **c,
                **creator_status(db, c["channel_id"]),
                "name": creators[c["channel_id"]].name if c["channel_id"] in creators else c["channel_id"],
                "last_error": creators[c["channel_id"]].last_error if c["channel_id"] in creators else "",
            }
            for c in user.config.get("creators", [])
        ],
        "feed": {
            "revision": feed.revision,
            "updated_at": feed.updated_at,
            "paused": feed.paused,
            "status": "updating"
            if pending
            else "error"
            if failed
            else "published"
            if feed.body
            else "pending",
            "event_count": sum(
                1
                for _ in db.scalars(
                    select(Projection.id).where(Projection.feed_id == feed.id, Projection.removed.is_(False))
                )
            ),
        },
    }


def attach_link(db, user: User, event: Event, url: str, title: str, kind: str):
    canonical, platform = canonical_url(url)
    user = lock_user(db, user.id)
    if not user or user.deleted:
        problem("NOT_FOUND", "账号已不可用", 404)
    link = db.scalar(
        select(Link)
        .where(Link.owner_id == user.id, Link.event_id == event.id, Link.url_hash == digest(canonical))
        .execution_options(populate_existing=True)
    )
    if link is None:
        link = Link(
            owner_id=user.id,
            event_id=event.id,
            url=canonical,
            url_hash=digest(canonical),
            title=title.strip() or f"{platform} 原链接",
            platform=platform,
            kind=kind,
        )
        db.add(link)
    else:
        link.title, link.kind = title.strip() or link.title, kind
    link.origin = "manual"
    link.available = True
    blocked = any(
        o["event_key"] == event.source_key and o["url"] == canonical and o["state"] == "block"
        for o in user.config["link_overrides"]
    )
    if not blocked:
        overrides = [
            o
            for o in user.config["link_overrides"]
            if (o["event_key"], o["url"]) != (event.source_key, canonical)
        ]
        overrides.append({"event_key": event.source_key, "url": canonical, "state": "pin"})
        save_config(db, user, {**user.config, "link_overrides": overrides}, user.revision)
    enqueue(db, "projection", {"user_id": user.id})
    db.flush()
    return link


def import_preview(db, user: User, data: ImportInput) -> tuple[dict, dict]:
    incoming = data.config.model_dump()
    config = incoming
    if data.mode == "merge":
        config = {
            **user.config,
            "preferences": {
                **user.config["preferences"],
                **data.config.preferences.model_dump(exclude_unset=True),
            },
        }
        keys = {
            "follows": lambda x: x["source_key"],
            "creators": lambda x: x["channel_id"],
            "event_overrides": lambda x: x["event_key"],
            "link_overrides": lambda x: (x["event_key"], x["url"]),
        }
        for name, key in keys.items():
            merged = {key(x): x for x in user.config.get(name, [])}
            merged.update({key(x): x for x in incoming[name]})
            config[name] = list(merged.values())
    unresolved = []
    for follow in config["follows"]:
        if not db.get(Source, follow["source_key"]) and not db.scalar(
            select(Event.id).where(Event.source_key == follow["source_key"])
        ):
            unresolved.append(follow["source_key"])
    for creator in config["creators"]:
        if not db.get(Creator, creator["channel_id"]):
            unresolved.append(creator["channel_id"])
    for item in config["event_overrides"] + config["link_overrides"]:
        if not db.scalar(select(Event.id).where(Event.source_key == item["event_key"])):
            unresolved.append(item["event_key"])
    for item in config["link_overrides"]:
        item["url"], _ = canonical_url(item["url"])
    summary = {
        "added": sum(
            x not in user.config.get(k, [])
            for k in ("follows", "creators", "event_overrides", "link_overrides")
            for x in config[k]
        ),
        "removed": sum(
            x not in config[k]
            for k in ("follows", "creators", "event_overrides", "link_overrides")
            for x in user.config.get(k, [])
        ),
        "unresolved": sorted(set(unresolved)),
        "revision": user.revision,
    }
    signature = digest(
        json.dumps({"user_id": user.id, "revision": user.revision, "config": config}, sort_keys=True)
    )
    # Encrypted, authenticated confirmation binds the exact preview to actor and revision.
    summary["confirmation"] = settings().cipher().encrypt(signature.encode()).decode()
    return config, summary
