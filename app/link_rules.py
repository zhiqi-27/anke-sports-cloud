"""Personal link selection semantics shared by SQL and document storage."""


def selected_links(event, config, links, published_info, owner_id=None):
    overrides = {
        x["url"]: x["state"] for x in config.get("link_overrides", []) if x["event_key"] == event.source_key
    }
    result = []
    for link in links:
        broadcast = None
        if link.kind not in {"live", "watch_along"}:
            continue
        if link.owner_id not in {owner_id, "public"} or not link.available:
            continue
        if link.owner_id == "public":
            broadcast = published_info(link)
            if broadcast is None:
                continue
        state = overrides.get(link.url)
        if state == "block":
            continue
        if event.status in {"cancelled", "postponed"} and link.origin == "discovery" and state != "pin":
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
            x["platform"],
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
