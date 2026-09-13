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

NBA_TEAM_IDS = {
    "ATL": 1610612737,
    "BOS": 1610612738,
    "CLE": 1610612739,
    "NOP": 1610612740,
    "CHI": 1610612741,
    "DAL": 1610612742,
    "DEN": 1610612743,
    "GSW": 1610612744,
    "HOU": 1610612745,
    "LAC": 1610612746,
    "LAL": 1610612747,
    "MIA": 1610612748,
    "MIL": 1610612749,
    "MIN": 1610612750,
    "BKN": 1610612751,
    "NYK": 1610612752,
    "ORL": 1610612753,
    "IND": 1610612754,
    "PHI": 1610612755,
    "PHX": 1610612756,
    "POR": 1610612757,
    "SAC": 1610612758,
    "SAS": 1610612759,
    "OKC": 1610612760,
    "TOR": 1610612761,
    "UTA": 1610612762,
    "MEM": 1610612763,
    "WAS": 1610612764,
    "DET": 1610612765,
    "CHA": 1610612766,
}


def nba_logo_url(short_name):
    team_id = NBA_TEAM_IDS.get(short_name)
    return f"https://cdn.nba.com/logos/nba/{team_id}/primary/L/logo.svg" if team_id else None


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

    def source(key, name, short, sport, kind, provider, color="#8bbdaa", logo_url=None):
        row = dict(
            id=key,
            name=name,
            short_name=short,
            sport=sport,
            kind=kind,
            provider=provider,
            color=color,
            logo_url=logo_url,
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
                        logo_url=nba_logo_url(team["abbreviation"]),
                    )
            start = instant.date()
            base_params = {
                "start_date": str(start - timedelta(days=7)),
                "end_date": str(start + timedelta(days=90)),
                "per_page": 100,
            }
            games = []
            # BALLDONTLIE omits preseason games unless explicitly requested.
            # Fetch both result sets as complete snapshots; never infer a game type
            # from its date or from the legacy postseason boolean.
            for season_type in (None, "preseason"):
                params = dict(base_params)
                if season_type:
                    params["season_type"] = season_type
                seen = set()
                for _ in range(100):
                    page = request(
                        client,
                        "https://api.balldontlie.io/v1/games",
                        headers={"Authorization": key},
                        params=params,
                    )
                    games.extend({**game, "_anke_season_type": season_type} for game in page["data"])
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
                        logo_url=nba_logo_url(game[k]["abbreviation"]),
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
                    title=("[季前赛] " if game["_anke_season_type"] == "preseason" else "")
                    + f"{participants[0]['name']} @ {participants[1]['name']}",
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
                            "delayed": "postponed",
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
            team_logos = {team["id"]: team.get("crest") for team in catalog["teams"]}
            for team in catalog["teams"]:
                source(
                    f"football-data:team:{team['id']}",
                    team["name"],
                    team.get("tla") or team["name"][:3],
                    "football",
                    "team",
                    provider,
                    logo_url=team.get("crest"),
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
                        logo_url=team_logos.get(match[k]["id"]),
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
