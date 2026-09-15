"""Personal link selection semantics shared by SQL and document storage."""

from app.calendar_rules import event_keys


def selected_links(event, config, links, published_info, owner_id=None):
    overrides = {
        x["url"]: x["state"] for x in config.get("link_overrides", []) if x["event_key"] == event.source_key
    }
    creators = {x["channel_id"]: x for x in config.get("creators", [])}
    result = []
    for link in links:
        broadcast = None
        if link.owner_id not in {owner_id, "public"} or not link.available:
            continue
        if link.owner_id == "public":
            broadcast = published_info(link)
            if broadcast is None:
                continue
        state = overrides.get(link.url)
        if state == "block":
            continue
        if link.origin == "automatic" and state != "pin":
            creator = creators.get(link.channel_id)
            if not creator or (link.kind in {"preview", "recap"} and not creator.get(link.kind, False)):
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
                "content_labels": getattr(link, "content_labels", []),
                "platform": link.platform,
                "creator": link.creator,
                "origin": link.origin,
                "access": link.access,
                "regions": link.regions,
                "pinned": state == "pin",
                "created_at": link.created_at,
            }
        )
    preferences = config.get("preferences", {})
    region = preferences.get("watch_region")
    platform_preferences = preferences.get("broadcast_platforms", {})
    preferred_platform = platform_preferences.get(
        f"{region}:{event.competition_id}" if region else event.competition_id
    )
    result.sort(
        key=lambda x: (
            not x["pinned"],
            not bool(x.get("broadcast") and x["broadcast"]["platform_id"] == preferred_platform),
            x["origin"] != "official",
            x["creator"],
            x["created_at"],
            x["id"],
        )
    )
    unique, seen_urls = [], set()
    for item in result:
        if item["url"] not in seen_urls:
            unique.append(item)
            seen_urls.add(item["url"])
    return unique
