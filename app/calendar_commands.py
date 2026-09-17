"""Shared domain rules for manual calendar sources."""

from copy import deepcopy

from app.calendar_rules import event_keys, manual_event_ids
from app.security import problem


def validate_manual_event(event, *, environment):
    """Only an existing, authoritative event can become a manual source."""
    if event is None:
        problem("EVENT_NOT_FOUND", "未找到这场比赛", 404)
    if event.demo and environment != "local":
        problem("EVENT_NOT_AVAILABLE", "只能添加真实赛程", 400)


def follow_sources(event, config):
    follows = {item["source_key"] for item in config.get("follows", [])}
    return sorted(event_keys(event) & follows)


def add_manual_source(config, event_id):
    current = manual_event_ids(config)
    if event_id in current:
        return deepcopy(config), False
    updated = deepcopy(config)
    updated["manual_events"] = [
        {"event_id": ident} for ident in sorted((*current, event_id))
    ]
    return updated, True


def remove_manual_source(config, event):
    covered = follow_sources(event, config)
    if covered:
        problem("EVENT_MANAGED_BY_FOLLOW", "这场比赛由球队关注管理，请取消关注后再操作", 409)
    current = manual_event_ids(config)
    if event.id not in current:
        problem("EVENT_NOT_MANUALLY_ADDED", "这场比赛没有手动添加到个人日历", 404)
    updated = deepcopy(config)
    updated["manual_events"] = [
        {"event_id": ident} for ident in sorted(current - {event.id})
    ]
    return updated, True
