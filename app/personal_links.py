"""Personal broadcast-link overrides used by the active SQL HTTP surface."""

from app.db import Event, Link
from app.security import problem
from app.service import save_config


def pin_link(db, user, link_id):
    link = db.get(Link, link_id)
    if not link or link.owner_id not in {user.id, "public"}:
        problem("NOT_FOUND", "未找到此链接", 404)
    event = db.get(Event, link.event_id)
    overrides = [
        row
        for row in user.config["link_overrides"]
        if (row["event_key"], row["url"]) != (event.source_key, link.url)
    ]
    overrides.append({"event_key": event.source_key, "url": link.url, "state": "pin"})
    save_config(db, user, {**user.config, "link_overrides": overrides}, user.revision)
