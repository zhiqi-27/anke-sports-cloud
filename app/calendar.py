import json
from datetime import date, datetime, timedelta, timezone

from icalendar import Calendar, Event as IcsEvent
from sqlalchemy import select, update

from app.db import BroadcastRecord, Event, Feed, Link, Projection, User, now
from app.schemas import Config
from app.security import digest


def event_keys(event: Event) -> set[str]:
    return {event.competition_id, event.source_key, *(x["id"] for x in event.participants)}


def included(event: Event, config: dict) -> bool:
    overrides = {x["event_key"]: x["state"] for x in config.get("event_overrides", [])}
    if event.source_key in overrides:
        return overrides[event.source_key] == "include"
    return bool(event_keys(event) & {x["source_key"] for x in config.get("follows", [])})


def chosen_links(db, event: Event, user: User | None) -> list[dict]:
    config = user.config if user else Config().model_dump()
    owners = ["public"] + ([user.id] if user else [])
    links = db.scalars(
        select(Link).where(Link.event_id == event.id, Link.owner_id.in_(owners), Link.available.is_(True))
    ).all()
    overrides = {
        x["url"]: x["state"] for x in config.get("link_overrides", []) if x["event_key"] == event.source_key
    }
    creators = {x["channel_id"]: x for x in config.get("creators", [])}
    result = []
    for link in links:
        broadcast = None
        if link.owner_id == "public":
            record = db.get(BroadcastRecord, link.id)
            if not record or record.status != "published" or not record.published:
                continue
            # Only the reviewed publication is authoritative, never an edited draft.
            if record.published["url"] != link.url:
                continue
            region = config.get("preferences", {}).get("watch_region")
            if (
                region
                and record.published["region_mode"] == "exclude"
                and region in record.published["regions"]
            ):
                continue
            from app.broadcasts import public_metadata

            broadcast = public_metadata(record)
        state = overrides.get(link.url)
        if state == "block":
            continue
        if link.origin == "automatic" and state != "pin":
            creator = creators.get(link.channel_id)
            if not creator or not creator.get(link.kind, False):
                continue
            if creator["scope_keys"] and not event_keys(event).intersection(creator["scope_keys"]):
                continue
        region = config.get("preferences", {}).get("watch_region")
        if region and link.regions and region not in link.regions:
            continue
        result.append(
            {
                "id": link.id,
                "broadcast": broadcast,
                "url": link.url,
                "title": link.title,
                "kind": link.kind,
                "platform": link.platform,
                "creator": link.creator,
                "origin": link.origin,
                "access": link.access,
                "regions": link.regions,
                "pinned": state == "pin",
                "created_at": link.created_at,
            }
        )
    result.sort(
        key=lambda x: (not x["pinned"], x["origin"] != "official", x["creator"], x["created_at"], x["id"])
    )
    unique, seen_urls = [], set()
    for item in result:
        if item["url"] not in seen_urls:
            unique.append(item)
            seen_urls.add(item["url"])
    return unique


def delivery_links(links: list[dict]) -> list[dict]:
    output = []
    for kinds, limit in [({"live", "watch_along"}, 2), ({"preview"}, 3), ({"recap"}, 3)]:
        group = [x for x in links if x["kind"] in kinds]
        selected, seen = [], set()
        for link in group:
            creator = link["creator"] or link["id"]
            if link["pinned"] or creator not in seen:
                selected.append(link)
                seen.add(creator)
        selected.extend(x for x in group if x not in selected)
        output.extend(selected[:limit])
    return output


def describe(event: Event, links: list[dict], config: dict) -> str:
    lines = []
    if event.demo:
        lines += ["演示赛程，用于界面与订阅测试，不代表真实比赛安排。", ""]
    labels = {
        "live": "观看直播",
        "watch_along": "同步解说（无比赛画面）",
        "preview": "赛前前瞻",
        "recap": "赛后复盘",
    }
    access = {
        "unknown": "观看条件未验证",
        "subscription": "需要订阅",
        "free": "免费",
        "login": "需要登录",
        "pay_per_view": "单次付费",
    }
    for kind, label in labels.items():
        group = [x for x in delivery_links(links) if x["kind"] == kind]
        if not group:
            continue
        lines.append(label)
        for link in group:
            title = (
                "赛后复盘"
                if kind == "recap" and config["preferences"].get("spoiler_free", True)
                else link["title"]
            )
            lines += [f"{link['creator'] or link['platform']} · {title}"]
            if kind in {"live", "watch_along"}:
                if link.get("broadcast"):
                    info = link["broadcast"]
                    lines += [
                        info["content_label"],
                        info["access_label"],
                        info["region_label"],
                        f"官方来源核验：{info['reviewed_at'][:10]}",
                        info["evidence_url"],
                    ]
                else:
                    lines += [access.get(link["access"], "观看条件未验证"), "地区限制未验证"]
            lines += [link["url"]]
        lines.append("")
    if not links:
        lines += ["暂无已确认的观看链接。", ""]
    lines += [f"预计时长 {event.duration} 分钟；开始时间以官方为准。", f"赛程来源：{event.provider}"]
    if event.source_url:
        lines.append(event.source_url)
    return "\n".join(lines)


def event_view(db, event: Event, user: User | None = None) -> dict:
    links = chosen_links(db, event, user)
    config = user.config if user else Config().model_dump()
    return {
        "id": event.id,
        "source_key": event.source_key,
        "competition_id": event.competition_id,
        "sport": event.sport,
        "title": event.title,
        "starts_at": event.starts_at,
        "local_date": event.local_date,
        "time_precision": event.time_precision,
        "timezone": event.timezone,
        "duration": event.duration,
        "venue": event.venue,
        "status": event.status,
        "participants": event.participants,
        "provider": event.provider,
        "source_url": event.source_url,
        "updated_at": event.updated_at,
        "demo": event.demo,
        "included": included(event, config) if user else False,
        "links": links,
        "description": describe(event, links, config),
        "description_in_feed": included(event, config) if user else False,
    }


def serialize(projections: list[Projection]) -> bytes:
    calendar = Calendar()
    calendar.add("prodid", "-//Anke Sports//Calendar 1.0//EN")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", "Anke Sports")
    calendar.add("refresh-interval", "PT6H", parameters={"VALUE": "DURATION"})
    for projection in sorted(projections, key=lambda x: x.id):
        data = projection.data
        if not data.get("starts_at") and not data.get("local_date"):
            continue
        event = IcsEvent()
        event.add("uid", f"{projection.id}@calendar.anke-sports")
        event.add("sequence", projection.version)
        changed = datetime.fromisoformat(projection.updated_at)
        event.add("dtstamp", changed)
        event.add("last-modified", changed)
        if data["time_precision"] == "exact" and data.get("starts_at"):
            start = datetime.fromisoformat(data["starts_at"].replace("Z", "+00:00"))
            event.add("dtstart", start)
            event.add("dtend", start + timedelta(minutes=data["duration"]))
        else:
            start = date.fromisoformat(data["local_date"])
            event.add("dtstart", start)
            event.add("dtend", start + timedelta(days=1))
        event.add(
            "summary",
            ("[已移除] " if projection.removed else "")
            + ("[时间待定] " if data["time_precision"] != "exact" else "")
            + data["title"],
        )
        event.add("description", data["description"])
        event.add("location", data["venue"])
        cancelled = projection.removed or data["status"] == "cancelled"
        event.add(
            "status",
            "CANCELLED" if cancelled else "TENTATIVE" if data["status"] == "postponed" else "CONFIRMED",
        )
        event.add(
            "transp",
            "TRANSPARENT"
            if data["transparent"] or cancelled or data["time_precision"] != "exact"
            else "OPAQUE",
        )
        if data.get("url"):
            event.add("url", data["url"])
        calendar.add_component(event)
    return calendar.to_ical()


def rebuild_feed(db, owner_id: str):
    # Acquire the owner's write lock before taking the configuration snapshot.
    # A no-op UPDATE also serializes local SQLite, where FOR UPDATE is ignored.
    db.execute(update(User).where(User.id == owner_id).values(revision=User.revision))
    user = db.scalar(
        select(User).where(User.id == owner_id).with_for_update().execution_options(populate_existing=True)
    )
    feed = db.scalar(select(Feed).where(Feed.owner_id == owner_id).with_for_update())
    if not user or user.deleted or not feed or feed.paused or feed.revoked:
        return
    config = user.config
    existing = {p.event_id: p for p in db.scalars(select(Projection).where(Projection.feed_id == feed.id))}
    lower = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
    upper = (datetime.now(timezone.utc) + timedelta(days=180)).date().isoformat()
    wanted = set()
    instant = datetime.now(timezone.utc).isoformat()
    overrides = {x["event_key"]: x["state"] for x in config.get("event_overrides", [])}
    events = db.scalars(select(Event)).all()
    for event in events:
        date_key = (event.starts_at or event.local_date or "")[:10]
        prior = existing.get(event.id)
        retain_history = bool(
            prior
            and not prior.removed
            and (event.starts_at or event.local_date or "9999") < instant
            and overrides.get(event.source_key) != "exclude"
        )
        if not (included(event, config) or retain_history) or not (lower <= date_key <= upper):
            continue
        wanted.add(event.id)
        links = chosen_links(db, event, user)
        target = next(
            (
                x["url"]
                for x in links
                if x.get("broadcast") and x["broadcast"]["content_type"] in {"official_match", "reservation"}
            ),
            event.source_url,
        )
        data = {
            "title": event.title,
            "starts_at": event.starts_at,
            "local_date": event.local_date,
            "time_precision": event.time_precision,
            "status": event.status,
            "duration": event.duration,
            "venue": event.venue,
            "description": describe(event, links, config),
            "transparent": config["preferences"]["transparent"],
            "url": target,
        }
        content_hash = digest(json.dumps(data, sort_keys=True, ensure_ascii=False))
        projection = existing.get(event.id)
        if not projection:
            projection = Projection(feed_id=feed.id, event_id=event.id, version=0)
            db.add(projection)
            existing[event.id] = projection
        if projection.content_hash != content_hash or projection.removed:
            projection.data = data
            projection.content_hash = content_hash
            projection.version += 1
            projection.updated_at = now()
            projection.removed = False
    for event_id, projection in existing.items():
        if event_id not in wanted and not projection.removed:
            projection.removed = True
            projection.version += 1
            projection.updated_at = now()
    db.flush()
    keep = [p for p in existing.values() if not p.removed or p.updated_at[:10] >= lower]
    body = serialize(keep)
    etag = digest(body.decode())
    if feed.etag != etag:
        feed.body = body.decode()
        feed.etag = etag
        feed.revision += 1
        feed.updated_at = now()
