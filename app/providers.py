import os
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import and_, case, func, select

from app.db import Event, Job, ProviderState, Source, now
from app.config import settings
from app.security import problem


def get_json(client, path, **kwargs):
    result = client.get(path, **kwargs)
    result.raise_for_status()
    return result.json()


def provider_key(name):
    # Explicit process configuration wins, including an empty value to disable it.
    value = os.getenv(name)
    if value is not None:
        return value
    field = {
        "BALLDONTLIE_API_KEY": "balldontlie_api_key",
        "FOOTBALL_DATA_API_KEY": "football_data_api_key",
        "YOUTUBE_API_KEY": "youtube_api_key",
    }[name]
    return getattr(settings(), field).get_secret_value()


def provider_statuses(db):
    instant = now()
    provider = Job.payload["provider"].as_string()
    activities = dict(
        db.execute(
            select(
                provider,
                func.max(
                    case(
                        (and_(Job.state == "running", Job.due_at > instant), 3),
                        (Job.due_at <= instant, 2),
                        else_=1,
                    )
                ),
            )
            .where(Job.kind == "provider", Job.state.in_(["pending", "running"]))
            .group_by(provider)
        ).all()
    )
    labels = {0: "idle", 1: "waiting", 2: "queued", 3: "running"}
    return [
        {
            "id": p.id,
            "last_success": p.last_success,
            "error": p.error,
            "enabled": p.enabled,
            "consecutive_failures": p.consecutive_failures,
            "next_attempt_at": p.next_attempt_at,
            "activity": labels[activities.get(p.id, 0)],
        }
        for p in db.scalars(select(ProviderState))
    ]


def source(db, key, name, short, sport, kind, provider, color="#8bbdaa"):
    obj = db.get(Source, key)
    if not obj:
        obj = Source(
            id=key,
            name=name,
            short_name=short,
            sport=sport,
            kind=kind,
            provider=provider,
            color=color,
            demo=False,
        )
        db.add(obj)
    return {"id": key, "name": name, "short_name": short, "color": color}


def upsert_event(db, key, **values):
    event = db.scalar(select(Event).where(Event.source_key == key))
    if event is None:
        event = Event(source_key=key, **values)
        db.add(event)
    elif any(getattr(event, k) != v for k, v in values.items()):
        for k, value in values.items():
            setattr(event, k, value)
        event.updated_at = now()


def sync_provider(db, provider):
    # Fetch all pages before touching rows; a failed page cannot publish a partial season.
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        if provider == "jolpica":
            year = datetime.now(timezone.utc).year
            data = get_json(client, f"https://api.jolpi.ca/ergast/f1/{year}/", params={"limit": 100})
            raw = data["MRData"]
            races = raw["RaceTable"]["Races"]
            if len(races) != int(raw["total"]):
                raise ValueError("INCOMPLETE_PAGINATION")
            if len({r["Circuit"]["circuitId"] for r in races}) != len(races):
                raise ValueError("AMBIGUOUS_CIRCUIT_IDENTITY")
            source(db, "jolpica:f1", "F1 世界锦标赛", "F1", "racing", "competition", provider, "#ec7972")
            for race in races:
                sessions = {
                    "race": {"date": race["date"], "time": race.get("time")},
                    **{
                        k: race[k]
                        for k in [
                            "FirstPractice",
                            "SecondPractice",
                            "ThirdPractice",
                            "Qualifying",
                            "Sprint",
                            "SprintQualifying",
                        ]
                        if race.get(k)
                    },
                }
                labels = {
                    "race": "正赛",
                    "FirstPractice": "自由练习 1",
                    "SecondPractice": "自由练习 2",
                    "ThirdPractice": "自由练习 3",
                    "Qualifying": "排位赛",
                    "Sprint": "冲刺赛",
                    "SprintQualifying": "冲刺排位赛",
                }
                for session, raw_time in sessions.items():
                    start = f"{raw_time['date']}T{raw_time['time']}" if raw_time.get("time") else None
                    if start:
                        datetime.fromisoformat(start.replace("Z", "+00:00"))
                    upsert_event(
                        db,
                        f"jolpica:{year}:{race['Circuit']['circuitId']}:{session}",
                        competition_id="jolpica:f1",
                        sport="racing",
                        title=f"{race['raceName']} · {labels[session]}",
                        starts_at=start,
                        local_date=raw_time["date"],
                        time_precision="exact" if start else "date_only",
                        duration=120 if session == "race" else 60,
                        venue=race["Circuit"]["circuitName"],
                        participants=[],
                        provider=provider,
                        source_url="https://www.formula1.com/en/racing/" + str(year),
                        demo=False,
                    )
        elif provider == "balldontlie":
            key = provider_key("BALLDONTLIE_API_KEY")
            if not key:
                raise ValueError("PROVIDER_KEY_REQUIRED")
            start = datetime.now(timezone.utc).date()
            params = {
                "start_date": str(start - timedelta(days=7)),
                "end_date": str(start + timedelta(days=90)),
                "per_page": 100,
            }
            games, seen = [], set()
            for _ in range(100):
                page = get_json(
                    client,
                    "https://api.balldontlie.io/v1/games",
                    headers={"Authorization": key},
                    params=params,
                )
                games.extend(page["data"])
                cursor = page.get("meta", {}).get("next_cursor")
                if cursor is None:
                    break
                if cursor in seen:
                    raise ValueError("PAGINATION_LOOP")
                seen.add(cursor)
                params["cursor"] = cursor
            else:
                raise ValueError("PAGINATION_LIMIT")
            source(db, "balldontlie:nba", "NBA", "NBA", "basketball", "competition", provider, "#f3b56a")
            for game in games:
                participants = [
                    source(
                        db,
                        f"balldontlie:team:{game[k]['id']}",
                        game[k]["full_name"],
                        game[k]["abbreviation"],
                        "basketball",
                        "team",
                        provider,
                    )
                    for k in ["visitor_team", "home_team"]
                ]
                start = game.get("datetime")
                if start:
                    datetime.fromisoformat(start.replace("Z", "+00:00"))
                upsert_event(
                    db,
                    f"balldontlie:game:{game['id']}",
                    competition_id="balldontlie:nba",
                    sport="basketball",
                    title=f"{participants[0]['name']} @ {participants[1]['name']}",
                    starts_at=start,
                    local_date=game["date"][:10],
                    time_precision="exact" if start else "date_only",
                    duration=150,
                    venue="",
                    status="finished" if game.get("status") == "Final" else "scheduled",
                    participants=participants,
                    provider=provider,
                    source_url="",
                    demo=False,
                )
        elif provider == "football-data":
            key = provider_key("FOOTBALL_DATA_API_KEY")
            if not key:
                raise ValueError("PROVIDER_KEY_REQUIRED")
            payload = get_json(
                client,
                "https://api.football-data.org/v4/competitions/PL/matches",
                headers={"X-Auth-Token": key},
            )
            matches = payload["matches"]
            source(
                db,
                "football-data:PL",
                "英格兰超级联赛",
                "英超",
                "football",
                "competition",
                provider,
                "#b3a0e2",
            )
            statuses = {
                "FINISHED": "finished",
                "CANCELLED": "cancelled",
                "POSTPONED": "postponed",
                "SUSPENDED": "postponed",
            }
            for match in matches:
                participants = [
                    source(
                        db,
                        f"football-data:team:{match[k]['id']}",
                        match[k]["name"],
                        match[k].get("tla") or match[k]["name"][:3],
                        "football",
                        "team",
                        provider,
                    )
                    for k in ["awayTeam", "homeTeam"]
                ]
                start = match.get("utcDate")
                precision = (
                    "exact"
                    if match["status"] != "SCHEDULED" and start
                    else "date_only"
                    if start
                    else "unknown"
                )
                upsert_event(
                    db,
                    f"football-data:match:{match['id']}",
                    competition_id="football-data:PL",
                    sport="football",
                    title=f"{participants[0]['name']} @ {participants[1]['name']}",
                    starts_at=start if precision == "exact" else None,
                    local_date=start[:10] if start else None,
                    time_precision=precision,
                    status=statuses.get(match["status"], "scheduled"),
                    duration=120,
                    venue="",
                    participants=participants,
                    provider=provider,
                    source_url="",
                    demo=False,
                )
        else:
            raise ValueError("UNKNOWN_PROVIDER")
    state = db.get(ProviderState, provider)
    if not state:
        state = ProviderState(id=provider)
        db.add(state)
    state.last_success, state.error, state.enabled = now(), "", True


def youtube_request(endpoint, params):
    key = provider_key("YOUTUBE_API_KEY")
    if not key:
        problem("YOUTUBE_KEY_REQUIRED", "YouTube 频道服务尚未配置，暂时无法读取创作者", 503)
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            return get_json(
                client, "https://www.googleapis.com/youtube/v3/" + endpoint, params={**params, "key": key}
            )
    except (httpx.HTTPError, ValueError):
        problem("YOUTUBE_API_UNAVAILABLE", "YouTube 暂时无法读取，请检查服务配置或稍后重试", 503)


def resolve_creator(value):
    import re
    from urllib.parse import urlsplit
    from app.security import canonical_url

    if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", value):
        params = {"id": value}
    elif value.startswith("@"):
        params = {"forHandle": value}
    else:
        parsed = urlsplit(value)
        if parsed.hostname not in {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}:
            problem("INVALID_CHANNEL", "请输入 YouTube 频道或视频链接")
        if parsed.path.startswith("/channel/"):
            params = {"id": parsed.path.split("/")[2]}
        elif parsed.path.startswith("/@"):
            params = {"forHandle": parsed.path.split("/")[1]}
        else:
            canonical, _ = canonical_url(value)
            video = youtube_request("videos", {"id": canonical.split("v=")[1], "part": "snippet"})["items"]
            if not video:
                problem("CHANNEL_NOT_FOUND", "无法读取此公开视频")
            params = {"id": video[0]["snippet"]["channelId"]}
    results = youtube_request("channels", {**params, "part": "snippet,contentDetails"})["items"]
    if not results:
        problem("CHANNEL_NOT_FOUND", "没有找到此公开频道")
    row = results[0]
    return {
        "channel_id": row["id"],
        "name": row["snippet"]["title"],
        "uploads_id": row["contentDetails"]["relatedPlaylists"]["uploads"],
    }
