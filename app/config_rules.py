"""Transport-independent configuration merge, preview and confirmation."""

from copy import deepcopy
import json

from pydantic import ValidationError

from app.schemas import Config
from app.security import canonical_url, digest, problem


def link_override(config, event_key, url, state):
    rows = [row for row in config["link_overrides"] if (row["event_key"], row["url"]) != (event_key, url)]
    rows.append({"event_key": event_key, "url": url, "state": state})
    return {**config, "link_overrides": rows}


def import_configuration(user, data, cipher, *, source_exists, event_exists):
    incoming = data.config.model_dump()
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
            "manual_events": lambda x: x["event_id"],
            "link_overrides": lambda x: (x["event_key"], x["url"]),
        }
        for name, key in keys.items():
            merged = {key(x): deepcopy(x) for x in user.config.get(name, [])}
            merged.update({key(x): x for x in incoming[name]})
            config[name] = list(merged.values())
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
        item["url"], _ = canonical_url(item["url"])
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
