"""Complete sports-provider fetches shared by SQL and document repositories.

Only returns normalized data after every page succeeds. No database imports.
"""

from datetime import datetime, timedelta, timezone
import os
from time import monotonic, sleep

import httpx

from app.config import settings

PROVIDERS = frozenset({"jolpica", "balldontlie", "football-data"})
PROVIDER_REFRESH = timedelta(hours=6)


def get_json(client, path, **kwargs):
    result = client.get(path, **kwargs)
    result.raise_for_status()
    return result.json()


def provider_key(name, cfg=None):
    value = os.getenv(name)
    if value is not None:
        return value
    field = {
        "BALLDONTLIE_API_KEY": "balldontlie_api_key",
        "FOOTBALL_DATA_API_KEY": "football_data_api_key",
        "YOUTUBE_API_KEY": "youtube_api_key",
    }[name]
    return getattr(cfg or settings(), field).get_secret_value()


def fetch_schedule(provider, *, request_json=None, key_reader=None, instant=None):
    instant = instant or datetime.now(timezone.utc)
    pace_requests = request_json is None
    request_json = request_json or get_json
    read_key = key_reader or provider_key
    deadline = monotonic() + 180
    events, sources = {}, {}
    last_request = None

    def request(client, path, **kwargs):
        nonlocal last_request
        interval = {"balldontlie": 13, "football-data": 6.1}.get(provider, 0)
        if pace_requests and last_request is not None and interval:
            wait = max(0, last_request + interval - monotonic())
            if monotonic() + wait >= deadline:
                raise ValueError("PROVIDER_FETCH_DEADLINE")
            sleep(wait)
        if monotonic() >= deadline:
            raise ValueError("PROVIDER_FETCH_DEADLINE")
        last_request = monotonic() if pace_requests else None
        result = request_json(client, path, **kwargs)
        if monotonic() >= deadline:
            raise ValueError("PROVIDER_FETCH_DEADLINE")
        return result

    def source(key, name, short, sport, kind, provider, color="#8bbdaa"):
        row = dict(
            id=key,
            name=name,
            short_name=short,
            sport=sport,
            kind=kind,
            provider=provider,
            color=color,
            demo=False,
        )
        if key in sources and sources[key] != row:
            raise ValueError("AMBIGUOUS_SOURCE_IDENTITY")
        sources[key] = row
        return {key: row[key] for key in ("id", "name", "short_name", "color")}

    def event(key, **values):
        if key in events:
            raise ValueError("AMBIGUOUS_EVENT_IDENTITY")
        row = {"source_key": key, "timezone": "UTC", "status": "scheduled", **values}
        if row["starts_at"] and not datetime.fromisoformat(row["starts_at"].replace("Z", "+00:00")).tzinfo:
            raise ValueError("PROVIDER_TIME_INVALID")
        if row["local_date"]:
            from datetime import date

            date.fromisoformat(row["local_date"])
        events[key] = row

    # Fetch all pages before touching rows; a failed page cannot publish a partial season.
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        if provider == "jolpica":
            year = instant.year
            data = request(client, f"https://api.jolpi.ca/ergast/f1/{year}/", params={"limit": 100})
            raw = data["MRData"]
            races = raw["RaceTable"]["Races"]
            if len(races) != int(raw["total"]):
                raise ValueError("INCOMPLETE_PAGINATION")
            if len({r["Circuit"]["circuitId"] for r in races}) != len(races):
                raise ValueError("AMBIGUOUS_CIRCUIT_IDENTITY")
            source("jolpica:f1", "F1 世界锦标赛", "F1", "racing", "competition", provider, "#ec7972")
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
                    event(
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
            key = read_key("BALLDONTLIE_API_KEY")
            if not key:
                raise ValueError("PROVIDER_KEY_REQUIRED")
            catalog = request(client, "https://api.balldontlie.io/v1/teams", headers={"Authorization": key})[
                "data"
            ]
            for team in catalog:
                # Historical franchises have no current conference/division assignment.
                if team.get("conference") in {"East", "West"} and team.get("division"):
                    source(
                        f"balldontlie:team:{team['id']}",
                        team["full_name"],
                        team["abbreviation"],
                        "basketball",
                        "team",
                        provider,
                    )
            start = instant.date()
            params = {
                "start_date": str(start - timedelta(days=7)),
                "end_date": str(start + timedelta(days=90)),
                "per_page": 100,
            }
            games, seen = [], set()
            for _ in range(100):
                page = request(
                    client,
                    "https://api.balldontlie.io/v1/games",
                    headers={"Authorization": key},
                    params=params,
                )
                games.extend(page["data"])
                cursor = page["meta"].get("next_cursor")
                if cursor is None:
                    break
                if cursor in seen:
                    raise ValueError("PAGINATION_LOOP")
                seen.add(cursor)
                params["cursor"] = cursor
            else:
                raise ValueError("PAGINATION_LIMIT")
            source("balldontlie:nba", "NBA", "NBA", "basketball", "competition", provider, "#f3b56a")
            for game in games:
                participants = [
                    source(
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
                event(
                    f"balldontlie:game:{game['id']}",
                    competition_id="balldontlie:nba",
                    sport="basketball",
                    title=f"{participants[0]['name']} @ {participants[1]['name']}",
                    starts_at=start,
                    local_date=game["date"][:10],
                    time_precision="exact" if start else "date_only",
                    duration=150,
                    venue="",
                    status=(
                        "postponed"
                        if game.get("postponed")
                        else {
                            "final": "finished",
                            "postponed": "postponed",
                            "suspended": "postponed",
                            "canceled": "cancelled",
                            "abandoned": "cancelled",
                        }.get(
                            game.get("status_state"),
                            "finished" if game.get("status") == "Final" else "scheduled",
                        )
                    ),
                    participants=participants,
                    provider=provider,
                    source_url="",
                    demo=False,
                )
        elif provider == "football-data":
            key = read_key("FOOTBALL_DATA_API_KEY")
            if not key:
                raise ValueError("PROVIDER_KEY_REQUIRED")
            catalog = request(
                client,
                "https://api.football-data.org/v4/competitions/PL/teams",
                headers={"X-Auth-Token": key},
            )
            season = catalog["season"]["startDate"][:4]
            if len(catalog["teams"]) != catalog["count"]:
                raise ValueError("INCOMPLETE_TEAM_CATALOG")
            for team in catalog["teams"]:
                source(
                    f"football-data:team:{team['id']}",
                    team["name"],
                    team.get("tla") or team["name"][:3],
                    "football",
                    "team",
                    provider,
                )
            payload = request(
                client,
                "https://api.football-data.org/v4/competitions/PL/matches",
                headers={"X-Auth-Token": key},
                params={"season": season},
            )
            matches = payload["matches"]
            if "resultSet" in payload and len(matches) != payload["resultSet"]["count"]:
                raise ValueError("INCOMPLETE_PAGINATION")
            source(
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
                event(
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
    return list(events.values()), list(sources.values())
