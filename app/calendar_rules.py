"""Calendar semantics shared by SQL and document persistence adapters."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event as IcsEvent

if TYPE_CHECKING:
    from app.db import Event, Projection


def event_keys(event: Event) -> set[str]:
    return {event.competition_id, event.source_key, *(x["id"] for x in event.participants)}


def inclusion_filter(config: dict):
    """Compile personal choices once for a candidate set, not once per event."""
    overrides = {x["event_key"]: x["state"] for x in config.get("event_overrides", [])}
    follows = {x["source_key"] for x in config.get("follows", [])}

    def accepts(event):
        if event.source_key in overrides:
            return overrides[event.source_key] == "include"
        return bool(event_keys(event) & follows)

    return accepts


def included(event: Event, config: dict) -> bool:
    return inclusion_filter(config)(event)


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


def serialize(projections: list[Projection], calendar_name="Anke Sports") -> bytes:
    calendar = Calendar()
    calendar.add("prodid", "-//Anke Sports//Calendar 1.0//EN")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", calendar_name)
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


def event_is_past(event, instant):
    if event.time_precision == "exact" and event.starts_at:
        start = datetime.fromisoformat(event.starts_at.replace("Z", "+00:00"))
        return bool(start.tzinfo and start < instant)
    # A date-only event today is not known to have happened yet.
    return bool(
        event.local_date
        and event.local_date < instant.astimezone(ZoneInfo(event.timezone)).date().isoformat()
    )


def select_candidates(events, config, existing, instant=None):
    instant = instant or datetime.now(timezone.utc)
    lower = (instant - timedelta(days=90)).date().isoformat()
    upper = (instant + timedelta(days=180)).date().isoformat()
    overrides = {x["event_key"]: x["state"] for x in config.get("event_overrides", [])}
    accepts = inclusion_filter(config)
    selected = []
    for event in events:
        date_key = (event.starts_at or event.local_date or "")[:10]
        prior = existing.get(event.id)
        retain_history = bool(
            prior
            and not prior.removed
            and event_is_past(event, instant)
            and overrides.get(event.source_key) != "exclude"
        )
        if (accepts(event) or retain_history) and lower <= date_key <= upper:
            selected.append(event)
    return selected, lower, upper


def projection_from_links(event, links, config):
    target = next(
        (
            x["url"]
            for x in links
            if x.get("broadcast") and x["broadcast"]["content_type"] in {"official_match", "reservation"}
        ),
        event.source_url,
    )
    return {
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
