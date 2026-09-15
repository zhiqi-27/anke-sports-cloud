"""Shared channel discovery and per-user match/override ownership."""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update

from app.calendar import chosen_links, event_keys, inclusion_filter
from app.db import (
    ChannelSync,
    ChannelWork,
    Creator,
    Event,
    Feed,
    Job,
    Link,
    Projection,
    Source,
    User,
    Video,
    VideoMatch,
    now,
)
from app.ai_matching import evaluate_with_ai
from app.config import settings
from app.matching import parse_time
from app.providers import youtube_request
from app.schemas import CreatorFollow
from app.youtube_content_rules import comment_sample, uploads_page, video_batch
from app.security import digest, problem
from app.service import enqueue, lock_user, save_config


def channel_users(db, channel_id, active_only=True):
    for user in db.scalars(select(User).where(User.deleted.is_(False)).order_by(User.id)):
        config = next((c for c in user.config["creators"] if c["channel_id"] == channel_id), None)
        if config and (not active_only or config["enabled"]):
            yield user, config


def channel_interest(db, channel_id, *, retained=False):
    """Read current column values, without retaining account locks during HTTP.

    Already-started upstream work may finish. Owner locks at mutation/enqueue
    boundaries keep its result from reviving an erased private account.
    """
    retained_owners = (
        set(db.scalars(select(Link.owner_id).where(Link.channel_id == channel_id, Link.owner_id != "public")))
        if retained
        else set()
    )
    for ident, config in db.execute(select(User.id, User.config).where(User.deleted.is_(False))):
        if ident in retained_owners or any(
            c["channel_id"] == channel_id and c["enabled"] for c in config.get("creators", [])
        ):
            return True
    return False


def enqueue_channel(db, channel_id, kind="youtube_poll"):
    if not db.get(ChannelSync, channel_id):
        db.add(ChannelSync(channel_id=channel_id))
        db.flush()
    # Serialize scheduling decisions across processes, including SQLite.
    db.execute(
        update(ChannelSync)
        .where(ChannelSync.channel_id == channel_id)
        .values(next_poll_at=ChannelSync.next_poll_at)
    )
    pending = db.scalar(
        select(Job.id)
        .where(
            Job.kind == kind,
            Job.state.in_(["pending", "running"]),
            Job.payload["channel_id"].as_string() == channel_id,
        )
        .limit(1)
    )
    if not pending:
        enqueue(db, kind, {"channel_id": channel_id})


def save_creator(db, user, details, scope_keys, preview, recap, enabled, revision, refresh_metadata=True):
    if revision != user.revision:
        problem("REVISION_CONFLICT", "配置已更新，请刷新后重试", 409)
    scope_keys = list(dict.fromkeys(scope_keys))
    followed_keys = {row["source_key"] for row in user.config.get("follows", [])}
    if not scope_keys:
        problem("CREATOR_SCOPE_REQUIRED", "请至少选择一个已关注对象")
    if any(key not in followed_keys for key in scope_keys):
        problem("CREATOR_SCOPE_NOT_FOLLOWED", "创作者只能关联当前已关注的对象", 409)
    for key in scope_keys:
        if not db.get(Source, key):
            problem("SOURCE_NOT_FOUND", "关联范围尚未接入")
    creator = db.get(Creator, details["channel_id"])
    if not creator:
        creator = Creator(**details)
        db.add(creator)
    elif refresh_metadata:
        creator.name, creator.uploads_id, creator.updated_at = details["name"], details["uploads_id"], now()
    row = CreatorFollow(
        channel_id=creator.channel_id, scope_keys=scope_keys, preview=preview, recap=recap, enabled=enabled
    ).model_dump()
    creators = [c for c in user.config["creators"] if c["channel_id"] != creator.channel_id] + [row]
    save_config(db, user, {**user.config, "creators": creators}, revision)
    if enabled:
        enqueue_channel(db, creator.channel_id)
    return creator


def remove_creator(db, user, channel_id, revision):
    save_config(
        db,
        user,
        {**user.config, "creators": [c for c in user.config["creators"] if c["channel_id"] != channel_id]},
        revision,
    )
    for match in db.scalars(
        select(VideoMatch)
        .join(Video, Video.id == VideoMatch.video_id)
        .where(
            VideoMatch.owner_id == user.id,
            Video.channel_id == channel_id,
            VideoMatch.decision == "needs_review",
        )
    ):
        match.decision = "retired"


def removal_impact(db, user, channel_id):
    links = db.scalars(select(Link).where(Link.owner_id == user.id, Link.channel_id == channel_id)).all()
    visible = {
        link["id"]
        for event_id in {link.event_id for link in links}
        for link in chosen_links(db, db.get(Event, event_id), user)
    }
    links = [link for link in links if link.id in visible]
    pins = {(o["event_key"], o["url"]) for o in user.config["link_overrides"] if o["state"] == "pin"}
    events = {e.id: e.source_key for e in db.scalars(select(Event))}
    removed = sum(
        link.origin == "automatic" and (events.get(link.event_id), link.url) not in pins for link in links
    )
    return {"automatic_removed": removed, "manual_retained": len(links) - removed, "revision": user.revision}


def match_video(db, video, only_user=None):
    url = f"https://www.youtube.com/watch?v={video.id}"
    creator = db.get(Creator, video.channel_id)
    if not creator:
        return
    # evaluate() accepts only [-3, +7] days around publication. Keep a generous
    # date margin for stored timezone offsets, plus ALL earlier associations so
    # title/date edits and unavailable videos can still retire stale matches.
    published = parse_time(video.published_at)
    lower = (published - timedelta(days=5)).date().isoformat()
    upper = (published + timedelta(days=9)).date().isoformat()
    all_events = db.scalars(
        select(Event).where(
            or_(
                and_(Event.starts_at >= lower, Event.starts_at < upper),
                Event.id.in_(select(VideoMatch.event_id).where(VideoMatch.video_id == video.id)),
                Event.id.in_(select(Link.event_id).where(Link.url_hash == digest(url))),
            )
        )
    ).all()
    event_by_id = {e.id: e for e in all_events}
    for user, follow in channel_users(db, video.channel_id):
        if only_user and user.id != only_user:
            continue
        user = lock_user(db, user.id)
        if not user or user.deleted:
            continue
        follow = next(
            (f for f in user.config["creators"] if f["channel_id"] == video.channel_id and f["enabled"]), None
        )
        if not follow:
            continue
        feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
        retained = (
            set(
                db.scalars(
                    select(Projection.event_id).where(
                        Projection.feed_id == feed.id, Projection.removed.is_(False)
                    )
                )
            )
            if feed
            else set()
        )
        accepts = inclusion_filter(user.config)
        candidates = [
            e
            for e in all_events
            if (accepts(e) or e.id in retained)
            and (not follow["scope_keys"] or event_keys(e).intersection(follow["scope_keys"]))
        ]
        outputs = (
            evaluate_with_ai(video, candidates, settings(), creator_name=creator.name)
            if video.available
            else []
        )
        existing = {
            m.event_id: m
            for m in db.scalars(
                select(VideoMatch)
                .where(VideoMatch.owner_id == user.id, VideoMatch.video_id == video.id)
                .execution_options(populate_existing=True)
            )
        }
        seen = set()
        links = {
            link.event_id: link
            for link in db.scalars(
                select(Link)
                .where(Link.owner_id == user.id, Link.url_hash == digest(url))
                .execution_options(populate_existing=True)
            )
        }
        overrides = {o["event_key"]: o["state"] for o in user.config["link_overrides"] if o["url"] == url}
        for output in outputs:
            eid = output["event_id"]
            seen.add(eid)
            state = overrides.get(event_by_id[eid].source_key)
            match = existing.get(eid)
            if match and match.decision in {"confirmed", "ignored"}:
                continue
            if not match:
                match = VideoMatch(owner_id=user.id, video_id=video.id, **output)
                db.add(match)
            else:
                for k, v in output.items():
                    setattr(match, k, v)
            if state == "block":
                match.decision = "ignored"
            if output["kind"] in {"preview", "recap"} and not follow.get(output["kind"], False):
                match.decision = "reject"
            match.updated_at = now()
            link = links.get(eid)
            if match.decision == "automatic":
                if not link:
                    link = Link(
                        owner_id=user.id,
                        event_id=eid,
                        url=url,
                        url_hash=digest(url),
                        title=video.title,
                        kind=output["kind"],
                        content_labels=output.get("content_labels", []),
                        platform="YouTube",
                        channel_id=video.channel_id,
                        creator=creator.name,
                        origin="automatic",
                    )
                    db.add(link)
                elif link.origin == "automatic" and state != "pin":
                    link.kind, link.content_labels, link.available = (
                        output["kind"],
                        output.get("content_labels", []),
                        True,
                    )
            elif link and link.origin == "automatic" and state != "pin":
                link.available = False
        for eid, match in existing.items():
            if eid not in seen and match.decision not in {"confirmed", "ignored"}:
                match.decision = "retired"
                match.updated_at = now()
                link = links.get(eid)
                if (
                    link
                    and link.origin == "automatic"
                    and overrides.get(event_by_id[eid].source_key) != "pin"
                ):
                    link.available = False
        enqueue(db, "projection", {"user_id": user.id})


def refresh_videos(db, channel_id, video_ids):
    ids = list(dict.fromkeys(video_ids))
    if not ids:
        return
    if len(ids) > 50:
        raise ValueError("VIDEO_BATCH_TOO_LARGE")
    if not channel_interest(db, channel_id, retained=True):
        return
    payload = youtube_request("videos", {"id": ",".join(ids), "part": "snippet,status"})
    existing = {ident: db.get(Video, ident) for ident in ids}
    previous = {
        ident: {
            name: getattr(video, name)
            for name in (
                "id",
                "channel_id",
                "title",
                "description",
                "comments",
                "published_at",
                "available",
                "updated_at",
            )
        }
        if video
        else None
        for ident, video in existing.items()
    }
    normalized = video_batch(channel_id, ids, payload, previous, now())
    for values in normalized:
        ident, public = values["id"], values["available"]
        old_comments = (previous.get(ident) or {}).get("comments", [])
        if public:
            try:
                values["comments"] = comment_sample(
                    youtube_request(
                        "commentThreads",
                        {
                            "videoId": ident,
                            "part": "snippet",
                            "maxResults": 12,
                            "order": "relevance",
                            "textFormat": "plainText",
                        },
                    )
                )
            except (HTTPException, ValueError):
                values["comments"] = list(old_comments)
        else:
            values["comments"] = []
        video = existing.get(ident)
        if video is None:
            video = Video(**values)
            db.add(video)
        else:
            for name, value in values.items():
                setattr(video, name, value)
        db.flush()
        url = f"https://www.youtube.com/watch?v={ident}"
        for link in db.scalars(select(Link).where(Link.url_hash == digest(url))):
            if not public or link.origin != "automatic":
                link.available = public
            elif not link.available:
                owner = db.get(User, link.owner_id)
                event = db.get(Event, link.event_id)
                if (
                    owner
                    and event
                    and any(
                        o["event_key"] == event.source_key and o["url"] == url and o["state"] == "pin"
                        for o in owner.config.get("link_overrides", [])
                    )
                ):
                    link.available = True
            if link.origin in {"automatic", "confirmed"}:
                link.title = video.title
            enqueue(db, "projection", {"user_id": link.owner_id})
        match_video(db, video)


def poll_channel(db, payload):
    channel_id = payload["channel_id"]
    creator = db.get(Creator, channel_id)
    if not creator or not channel_interest(db, channel_id):
        return
    cutoff = payload.get("cutoff") or (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    args = {"playlistId": creator.uploads_id, "part": "contentDetails", "maxResults": 50}
    if payload.get("cursor"):
        args["pageToken"] = payload["cursor"]
    raw = youtube_request("playlistItems", args)
    seen = payload.get("seen", [])
    ids, cursor = uploads_page(raw, cutoff, seen)
    # Each successful uploads page commits independent detail work before quota waits.
    if ids:
        enqueue(db, "youtube_videos", {"channel_id": channel_id, "video_ids": ids})
    if cursor:
        enqueue(
            db,
            "youtube_poll",
            {"channel_id": channel_id, "cursor": cursor, "cutoff": cutoff, "seen": [*seen, cursor]},
        )
    else:
        sync = db.get(ChannelSync, channel_id)
        if not sync:
            sync = ChannelSync(channel_id=channel_id)
            db.add(sync)
        sync.last_success, sync.error, sync.next_poll_at = (
            now(),
            "",
            (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),
        )
        creator.last_error = ""
        # Refresh known retained videos: absence from uploads is never a deletion signal.
        retained_ids = list(
            db.scalars(select(Video.id).where(Video.channel_id == channel_id, Video.id.not_in(ids)))
        )
        for offset in range(0, len(retained_ids), 50):
            enqueue(
                db,
                "youtube_videos",
                {"channel_id": channel_id, "video_ids": retained_ids[offset : offset + 50]},
            )
        enqueue(db, "youtube_channel_metadata", {"channel_id": channel_id})


def refresh_channel_metadata(db, channel_id):
    creator = db.get(Creator, channel_id)
    if not creator or not channel_interest(db, channel_id, retained=True):
        return
    items = youtube_request("channels", {"id": channel_id, "part": "snippet,contentDetails"})["items"]
    if len(items) != 1 or items[0]["id"] != channel_id:
        raise ValueError("CHANNEL_UNAVAILABLE")
    creator.name = items[0]["snippet"]["title"][:160]
    creator.updated_at = now()
    creator.uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    for link in db.scalars(select(Link).where(Link.channel_id == channel_id)):
        link.creator = creator.name
        enqueue(db, "projection", {"user_id": link.owner_id})


def review_list(db, user):
    rows = db.scalars(
        select(VideoMatch)
        .where(VideoMatch.owner_id == user.id, VideoMatch.decision == "needs_review")
        .order_by(VideoMatch.updated_at.desc())
        .limit(200)
    ).all()
    result = []
    follows = {row["channel_id"]: row for row in user.config.get("creators", [])}
    for row in rows:
        video = db.get(Video, row.video_id)
        event = db.get(Event, row.event_id)
        if not video or not video.available or not event:
            continue
        follow = follows.get(video.channel_id)
        if not follow or not event_keys(event).intersection(follow["scope_keys"]):
            continue
        creator = db.get(Creator, video.channel_id)
        result.append(
            {
                "id": row.id,
                "video_id": video.id,
                "title": video.title,
                "url": f"https://www.youtube.com/watch?v={video.id}",
                "creator": creator.name if creator else video.channel_id,
                "published_at": video.published_at,
                "event_id": event.id,
                "event_title": event.title,
                "starts_at": event.starts_at,
                "kind": row.kind,
                "content_labels": row.content_labels,
                "reason_codes": row.reason_codes,
                "rule_version": row.rule_version,
                "updated_at": row.updated_at,
            }
        )
    return {"items": result}


def decide_review(db, user, match_id, decision, kind, version):
    user = lock_user(db, user.id)
    if not user or user.deleted:
        problem("NOT_FOUND", "账号已不可用", 404)
    row = db.scalar(
        select(VideoMatch).where(VideoMatch.id == match_id).execution_options(populate_existing=True)
    )
    if not row or row.owner_id != user.id:
        problem("NOT_FOUND", "未找到待确认内容", 404)
    if row.updated_at != version or row.decision != "needs_review":
        problem("REVIEW_CHANGED", "内容已更新，请重新查看", 409)
    video = db.get(Video, row.video_id)
    event = db.get(Event, row.event_id)
    if not video or not video.available:
        problem("VIDEO_UNAVAILABLE", "视频已不可用", 409)
    url = f"https://www.youtube.com/watch?v={video.id}"
    overrides = [
        o for o in user.config["link_overrides"] if (o["event_key"], o["url"]) != (event.source_key, url)
    ]
    overrides.append(
        {"event_key": event.source_key, "url": url, "state": "pin" if decision == "confirm" else "block"}
    )
    save_config(db, user, {**user.config, "link_overrides": overrides}, user.revision)
    row.decision = "confirmed" if decision == "confirm" else "ignored"
    row.updated_at = now()
    if decision == "confirm":
        creator = db.get(Creator, video.channel_id)
        link = db.scalar(
            select(Link).where(
                Link.owner_id == user.id, Link.event_id == event.id, Link.url_hash == digest(url)
            )
        )
        if not link:
            link = Link(
                owner_id=user.id,
                event_id=event.id,
                url=url,
                url_hash=digest(url),
                title=video.title,
                platform="YouTube",
                kind="video",
                content_labels=row.content_labels,
            )
            db.add(link)
        link.origin, link.kind, link.content_labels, link.available, link.channel_id, link.creator = (
            "confirmed",
            "video",
            row.content_labels,
            True,
            video.channel_id,
            creator.name,
        )


def pin_link(db, user, link_id):
    link = db.get(Link, link_id)
    if not link or link.owner_id not in {user.id, "public"}:
        problem("NOT_FOUND", "未找到此链接", 404)
    event = db.get(Event, link.event_id)
    overrides = [
        o for o in user.config["link_overrides"] if (o["event_key"], o["url"]) != (event.source_key, link.url)
    ]
    overrides.append({"event_key": event.source_key, "url": link.url, "state": "pin"})
    save_config(db, user, {**user.config, "link_overrides": overrides}, user.revision)


def creator_status(db, channel_id):
    sync = db.get(ChannelSync, channel_id)
    data_work = db.get(ChannelWork, channel_id + ":data")
    hub_work = db.get(ChannelWork, channel_id + ":hub")
    data_error = data_work.error if data_work else (sync.error if sync else "")
    pending = db.scalar(
        select(Job.id)
        .where(
            Job.kind.in_(["youtube_poll", "youtube_videos", "youtube_channel_metadata"]),
            Job.state.in_(["pending", "running"]),
            Job.payload["channel_id"].as_string() == channel_id,
        )
        .limit(1)
    )
    return {
        "sync_status": "syncing"
        if pending
        else "error"
        if data_error
        else "current"
        if sync and sync.last_success
        else "pending",
        "last_synced_at": sync.last_success if sync else None,
        "websub_status": "error" if hub_work and hub_work.error else sync.state if sync else "disabled",
    }


def expire_metadata(db):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=28)).isoformat()
    for video in db.scalars(select(Video).where(Video.updated_at < cutoff, Video.available.is_(True))):
        changed = db.execute(
            update(Video)
            .where(Video.id == video.id, Video.updated_at == video.updated_at, Video.available.is_(True))
            .values(title="元数据已过期", description="", comments=[], available=False)
            .execution_options(synchronize_session=False)
        )
        if not changed.rowcount:
            continue  # A refresh committed after this maintenance scan read its candidate.
        db.refresh(video)
        url = f"https://www.youtube.com/watch?v={video.id}"
        for link in db.scalars(select(Link).where(Link.url_hash == digest(url))):
            link.available = False
            if link.origin in {"automatic", "confirmed"}:
                link.title = "元数据已过期"
            enqueue(db, "projection", {"user_id": link.owner_id})
        for match in db.scalars(
            select(VideoMatch).where(
                VideoMatch.video_id == video.id, VideoMatch.decision.in_(["automatic", "needs_review"])
            )
        ):
            match.decision = "retired"
    for creator in db.scalars(
        select(Creator).where(Creator.updated_at < cutoff, Creator.name != Creator.channel_id)
    ):
        changed = db.execute(
            update(Creator)
            .where(Creator.channel_id == creator.channel_id, Creator.updated_at == creator.updated_at)
            .values(name=creator.channel_id)
            .execution_options(synchronize_session=False)
        )
        if not changed.rowcount:
            continue
        db.refresh(creator)
        for link in db.scalars(select(Link).where(Link.channel_id == creator.channel_id)):
            link.creator = "YouTube"
            enqueue(db, "projection", {"user_id": link.owner_id})
