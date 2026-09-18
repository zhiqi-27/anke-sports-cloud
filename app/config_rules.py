"""Transport-independent configuration merge, preview and confirmation."""

from copy import deepcopy
import json

from pydantic import ValidationError

from app.schemas import Config, Follow, LinkOverride, ManualEventSource, Preferences
from app.security import digest, personal_url, problem


def link_override(config, event_key, url, state):
    rows = [row for row in config["link_overrides"] if (row["event_key"], row["url"]) != (event_key, url)]
    rows.append({"event_key": event_key, "url": url, "state": state})
    return {**config, "link_overrides": rows}


def normalize_stored_config(value):
    """Project an older persisted config into the current public contract.

    This is deliberately a read-time boundary, not an import or migration path:
    retired video fields and unsupported follow types never re-enter the current
    API, while the original document remains untouched until the user performs a
    current configuration mutation.
    """
    if not isinstance(value, dict):
        return Config().model_dump()

    defaults = Preferences().model_dump()
    stored_preferences = value.get("preferences")
    if isinstance(stored_preferences, dict):
        preferences = {key: stored_preferences[key] for key in defaults if key in stored_preferences}
        try:
            preferences = Preferences.model_validate({**defaults, **preferences}).model_dump()
        except ValidationError:
            preferences = defaults
    else:
        preferences = defaults

    follows, follow_keys = [], set()
    for raw in value.get("follows", []):
        try:
            follow = Follow.model_validate(raw)
        except ValidationError:
            continue
        if follow.source_key in follow_keys:
            continue
        follow_keys.add(follow.source_key)
        follows.append(follow.model_dump())

    manual_events, manual_keys = [], set()
    for raw in value.get("manual_events", []):
        try:
            event = ManualEventSource.model_validate(raw)
        except ValidationError:
            continue
        if event.event_id in manual_keys:
            continue
        manual_keys.add(event.event_id)
        manual_events.append(event.model_dump())

    link_overrides, link_keys = [], set()
    for raw in value.get("link_overrides", []):
        try:
            link = LinkOverride.model_validate(raw)
        except ValidationError:
            continue
        key = (link.event_key, link.url)
        if key in link_keys:
            continue
        link_keys.add(key)
        link_overrides.append(link.model_dump())

    return Config.model_validate(
        {
            "schema_version": 1,
            "follows": follows,
            "preferences": preferences,
            "manual_events": manual_events,
            "link_overrides": link_overrides,
        }
    ).model_dump()


def import_configuration(user, data, cipher, *, source_exists, event_exists):
    incoming = data.config.model_dump()
    current_manual = {row["event_id"] for row in user.config.get("manual_events", [])}
    if "manual_events" in data.config.model_fields_set:
        requested_manual = {row["event_id"] for row in incoming["manual_events"]}
        if requested_manual != current_manual:
            problem("MANUAL_EVENTS_IMPORT_FORBIDDEN", "手动日历来源只能通过单场日历操作管理", 409)
    config = incoming
    if data.mode == "merge":
        config = {
            **deepcopy(user.config),
            "preferences": {
                **user.config["preferences"],
                **data.config.preferences.model_dump(exclude_unset=True),
            },
        }
        keys = {
            "follows": lambda x: x["source_key"],
            "link_overrides": lambda x: (x["event_key"], x["url"]),
        }
        for name, key in keys.items():
            merged = {key(x): deepcopy(x) for x in user.config.get(name, [])}
            merged.update({key(x): x for x in incoming[name]})
            config[name] = list(merged.values())
    # Manual sources have their own authorization and lifecycle. They may be
    # carried by an exact export/import round trip, but never changed here.
    config["manual_events"] = deepcopy(user.config.get("manual_events", []))
    unresolved = []
    for follow in config["follows"]:
        if not source_exists(follow["source_key"]):
            unresolved.append(follow["source_key"])
    for item in config["manual_events"]:
        if not event_exists(item["event_id"]):
            unresolved.append(item["event_id"])
    for item in config["link_overrides"]:
        if not event_exists(item["event_key"]):
            unresolved.append(item["event_key"])
    for item in config["link_overrides"]:
        item["url"], _ = personal_url(item["url"])
    try:
        config = Config.model_validate(config).model_dump()
    except ValidationError:
        problem("CONFIG_LIMIT_EXCEEDED", "合并后的配置超过支持的数量上限，请减少内容后重试")
    summary = {
        "added": sum(
            x not in user.config.get(k, [])
            for k in ("follows", "manual_events", "link_overrides")
            for x in config[k]
        ),
        "removed": sum(
            x not in config[k]
            for k in ("follows", "manual_events", "link_overrides")
            for x in user.config.get(k, [])
        ),
        "unresolved": sorted(set(unresolved)),
        "revision": user.revision,
    }
    signature = digest(
        json.dumps({"user_id": user.id, "revision": user.revision, "config": config}, sort_keys=True)
    )
    summary["confirmation"] = cipher.encrypt(signature.encode()).decode()
    return config, summary


def confirm_import(data, preview, cipher):
    try:
        valid = cipher.decrypt((data.confirmation or "").encode()) == cipher.decrypt(
            preview["confirmation"].encode()
        )
    except Exception:
        valid = False
    if not valid:
        problem("PREVIEW_REQUIRED", "请先预览并确认本次导入")
    if preview["unresolved"]:
        problem("UNRESOLVED_CONFIG", "存在无法解析的对象，请修正后导入")
