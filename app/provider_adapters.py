"""Complete sports-provider fetches shared by SQL and document repositories.

Only returns normalized data after every page succeeds. No database imports.
"""

from datetime import datetime, timedelta, timezone
import os
from time import monotonic, sleep

import httpx

from app.config import settings
from app.source_rules import source_logo_url

PROVIDERS = frozenset({"jolpica", "balldontlie", "football-data"})
PROVIDER_REFRESH = timedelta(hours=6)
RESULT_REFRESH = timedelta(minutes=15)
RESULT_REFRESH_BEFORE = timedelta(hours=2)
RESULT_REFRESH_AFTER = timedelta(hours=4)
F1_TEAM_SHORT_NAMES = {
    "alpine": "ALP",
    "aston_martin": "AMR",
    "audi": "AUD",
    "cadillac": "CAD",
    "ferrari": "FER",
    "haas": "HAS",
    "mclaren": "MCL",
    "mercedes": "MER",
    "racing_bulls": "RB",
    "red_bull": "RBR",
    "williams": "WIL",
}


def completed_result(away_score, home_score, winner=None):
    """Return a result only when both provider scores are complete and valid."""
    if not all(
        isinstance(score, int) and not isinstance(score, bool) and score >= 0
        for score in (away_score, home_score)
    ):
        return None
    normalized_winner = {"AWAY": "away", "HOME": "home", "DRAW": "draw"}.get(
        str(winner or "").upper()
    )
    if not normalized_winner:
        normalized_winner = (
            "away" if away_score > home_score else "home" if home_score > away_score else "draw"
        )
    return {
        "away_score": away_score,
        "home_score": home_score,
        "winner": normalized_winner,
    }


def provider_refresh_interval(provider, events, instant):
    """Poll team-sport events more often around their completion window."""
    lower, upper = instant - RESULT_REFRESH_BEFORE, instant + RESULT_REFRESH_AFTER
    for event in events:
        if getattr(event, "provider", None) != provider or getattr(event, "sport", None) not in {
            "football",
            "basketball",
        }:
            continue
        value = getattr(event, "starts_at", None)
        if value:
            try:
                start = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        else:
            local_date = getattr(event, "local_date", None)
            if not local_date:
                continue
            try:
                start = datetime.fromisoformat(local_date).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        if lower <= start <= upper:
            return RESULT_REFRESH
    return PROVIDER_REFRESH


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
            logo_url=source_logo_url(key, short, logo_url),
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
            constructor_data = request(
                client,
                f"https://api.jolpi.ca/ergast/f1/{year}/constructors/",
                params={"limit": 100},
            )["MRData"]
            constructors = constructor_data["ConstructorTable"]["Constructors"]
            if len(constructors) != int(constructor_data["total"]) or not constructors:
                raise ValueError("INCOMPLETE_CONSTRUCTOR_CATALOG")
            if len({row["constructorId"] for row in constructors}) != len(constructors):
                raise ValueError("AMBIGUOUS_CONSTRUCTOR_IDENTITY")
            participants = [
                source(
                    f"jolpica:constructor:{row['constructorId']}",
                    row["name"],
                    F1_TEAM_SHORT_NAMES.get(row["constructorId"], row["constructorId"][:3].upper()),
                    "racing",
                    "team",
                    provider,
                )
                for row in constructors
            ]
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
                        participants=participants,
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
            nba_team_ids = set()
            for team in catalog:
                # Historical franchises have no current conference/division assignment.
                if team.get("conference") in {"East", "West"} and team.get("division"):
                    nba_team_ids.add(team["id"])
                    source(
                        f"balldontlie:team:{team['id']}",
                        team["full_name"],
                        team["abbreviation"],
                        "basketball",
                        "team",
                        provider,
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
                participants = []
                for key in ["visitor_team", "home_team"]:
                    team = game[key]
                    source_key = f"balldontlie:team:{team['id']}"
                    if team["id"] in nba_team_ids:
                        participant = source(
                            source_key,
                            team["full_name"],
                            team["abbreviation"],
                            "basketball",
                            "team",
                            provider,
                        )
                    else:
                        participant = {
                            "id": source_key,
                            "name": team["full_name"],
                            "short_name": team["abbreviation"],
                            "color": "#8bbdaa",
                        }
                    participants.append(participant)
                start = game.get("datetime")
                if start:
                    datetime.fromisoformat(start.replace("Z", "+00:00"))
                status = (
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
                )
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
                    status=status,
                    result=(
                        completed_result(game.get("visitor_team_score"), game.get("home_team_score"))
                        if status == "finished"
                        else None
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
            catalog_team_ids = {team["id"] for team in catalog["teams"]}
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
            seen_match_signatures = {}

            def competition_for(match, fallback_code):
                details = match.get("competition") or {}
                code = str(details.get("code") or fallback_code)
                source_key = f"football-data:{code}"
                if source_key not in sources:
                    if code == "PL":
                        source(
                            source_key,
                            "英格兰超级联赛",
                            "英超",
                            "football",
                            "competition",
                            provider,
                            "#b3a0e2",
                        )
                    else:
                        labels = {
                            "CL": ("欧冠", "欧冠"),
                            "FAC": ("足总杯", "足总杯"),
                            "ELC": ("英格兰联赛杯", "联赛杯"),
                        }
                        name, short = labels.get(
                            code,
                            (details.get("name") or code, details.get("code") or code[:8]),
                        )
                        colors = {"CL": "#79a9df", "FAC": "#9cc6a2"}
                        source(
                            source_key,
                            name,
                            short,
                            "football",
                            "competition",
                            provider,
                            colors.get(code, "#8bbdaa"),
                            logo_url=details.get("emblem"),
                        )
                return source_key, code

            def participant_for(team):
                source_key = f"football-data:team:{team['id']}"
                short = team.get("tla") or team["name"][:3]
                if team["id"] in catalog_team_ids:
                    return source(
                        source_key,
                        team["name"],
                        short,
                        "football",
                        "team",
                        provider,
                        logo_url=team_logos.get(team["id"]) or team.get("crest"),
                    )
                return {
                    "id": source_key,
                    "name": team["name"],
                    "short_name": short,
                    "color": "#8bbdaa",
                    "logo_url": team.get("crest"),
                }

            def add_match(match, fallback_code):
                match_key = f"football-data:match:{match['id']}"
                competition_id, competition_code = competition_for(match, fallback_code)
                participants = [participant_for(match[k]) for k in ["awayTeam", "homeTeam"]]
                signature = (
                    competition_code,
                    match.get("status"),
                    match.get("utcDate"),
                    tuple(participant["id"] for participant in participants),
                )
                if match_key in seen_match_signatures:
                    if seen_match_signatures[match_key] != signature:
                        raise ValueError("AMBIGUOUS_EVENT_IDENTITY")
                    return
                seen_match_signatures[match_key] = signature
                start = match.get("utcDate")
                precision = (
                    "exact"
                    if match["status"] != "SCHEDULED" and start
                    else "date_only"
                    if start
                    else "unknown"
                )
                status = statuses.get(match["status"], "scheduled")
                score = match.get("score") or {}
                full_time = score.get("fullTime") or {}
                event(
                    match_key,
                    competition_id=competition_id,
                    sport="football",
                    title=f"{participants[0]['name']} @ {participants[1]['name']}",
                    starts_at=start if precision == "exact" else None,
                    local_date=start[:10] if start else None,
                    time_precision=precision,
                    status=status,
                    result=(
                        completed_result(full_time.get("away"), full_time.get("home"), score.get("winner"))
                        if status == "finished"
                        else None
                    ),
                    duration=120,
                    venue="",
                    participants=participants,
                    provider=provider,
                    source_url="",
                    demo=False,
                )

            for match in matches:
                add_match(match, "PL")

            # The competition endpoint only covers the Premier League. Each
            # current PL team's schedule also includes the other competitions
            # returned by football-data (for example CL and FAC). Deduplicate
            # by the upstream match ID so the stable event identity survives
            # the overlap between team schedules and the PL snapshot.
            for team in catalog["teams"]:
                team_payload = request(
                    client,
                    f"https://api.football-data.org/v4/teams/{team['id']}/matches",
                    headers={"X-Auth-Token": key},
                    params={"season": season},
                )
                team_matches = team_payload["matches"]
                if "resultSet" in team_payload and len(team_matches) != team_payload["resultSet"]["count"]:
                    raise ValueError("INCOMPLETE_PAGINATION")
                for match in team_matches:
                    add_match(match, "PL")
        else:
            raise ValueError("UNKNOWN_PROVIDER")
    return list(events.values()), list(sources.values())
