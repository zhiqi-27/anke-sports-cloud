from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.calendar import event_view
from app.calendar_rules import calendar_title, projection_from_links, spoiler_hidden_event_ids
from app.db import Event, User
from app.provider_adapters import RESULT_REFRESH, PROVIDER_REFRESH, provider_refresh_interval
from app.schemas import Config


def result_event(**overrides):
    values = dict(
        id="result-event",
        source_key="fixture:team:away",
        competition_id="fixture:league",
        sport="football",
        title="客队 @ 主队",
        starts_at="2026-09-18T10:00:00+00:00",
        local_date="2026-09-18",
        time_precision="exact",
        timezone="UTC",
        duration=120,
        venue="",
        status="finished",
        participants=[],
        provider="fixture",
        source_url="",
        demo=False,
        result={"away_score": 1, "home_score": 2, "winner": "home"},
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_spoiler_protection_only_hides_scoped_latest_result():
    event = result_event()
    config = Config().model_dump()
    config["follows"] = [{"type": "team", "source_key": event.source_key}]
    hidden = spoiler_hidden_event_ids([event], config)
    assert hidden == {event.id}
    assert calendar_title(event, config, hidden_result_ids=hidden) == "客队 @ 主队"
    assert calendar_title(event, config, hidden_result_ids=set()) == "客队 @ 主队 · 主胜 1–2"
    assert calendar_title(event, config, personal=False) == "客队 @ 主队"
    assert calendar_title(result_event(result=None), config, hidden_result_ids=set()) == "客队 @ 主队"


def test_spoiler_protection_keeps_older_results_and_ignores_unfollowed_events():
    config = Config().model_dump()
    config["follows"] = [{"type": "team", "source_key": "fixture:team:away"}]
    older = result_event(id="older", starts_at="2026-09-17T10:00:00+00:00")
    latest = result_event(id="latest", starts_at="2026-09-18T10:00:00+00:00")
    other = result_event(id="other", source_key="fixture:team:other")
    assert spoiler_hidden_event_ids([older, latest, other], config) == {"latest"}
    config["preferences"]["spoiler_free"] = False
    assert spoiler_hidden_event_ids([older, latest, other], config) == set()


def test_projection_title_follows_personal_boundary():
    config = Config().model_dump()
    config["preferences"]["spoiler_free"] = False
    assert projection_from_links(result_event(), [], config)["title"] == "客队 @ 主队 · 主胜 1–2"
    assert projection_from_links(result_event(), [], config, personal=False)["title"] == "客队 @ 主队"


def test_sql_event_view_does_not_leak_result_to_other_or_public_user(stack):
    _, sessions = stack
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        event = Event(
            source_key="fixture:result-match-latest",
            competition_id="fixture:league",
            sport="football",
            title="客队 @ 主队",
            starts_at="2026-09-18T10:00:00+00:00",
            local_date="2026-09-18",
            status="finished",
            participants=[{"id": "fixture:result-team", "name": "关注球队"}],
            provider="fixture",
            result={"away_score": 1, "home_score": 2, "winner": "home"},
        )
        older = Event(
            source_key="fixture:result-match-older",
            competition_id="fixture:league",
            sport="football",
            title="客队 @ 主队",
            starts_at="2026-09-17T10:00:00+00:00",
            local_date="2026-09-17",
            status="finished",
            participants=[{"id": "fixture:result-team", "name": "关注球队"}],
            provider="fixture",
            result={"away_score": 0, "home_score": 1, "winner": "home"},
        )
        db.add_all([older, event])
        db.flush()
        config = {
            **user.config,
            "follows": [{"type": "team", "source_key": "fixture:result-team"}],
        }
        user.config = config
        db.commit()
        personal = event_view(db, event, user)
        older_personal = event_view(db, older, user)
        public = event_view(db, event)
    assert personal["title"] == "客队 @ 主队"
    assert personal["result"] is None
    assert older_personal["title"] == "客队 @ 主队 · 主胜 0–1"
    assert older_personal["result"]["home_score"] == 1
    assert public["title"] == "客队 @ 主队"
    assert public["result"] is None


def test_result_refresh_window_is_shorter_only_for_nearby_team_events():
    instant = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
    nearby = result_event(
        provider="balldontlie",
        sport="basketball",
        starts_at=(instant - timedelta(minutes=30)).isoformat(),
    )
    far = result_event(provider="balldontlie", starts_at=(instant + timedelta(days=2)).isoformat())
    assert provider_refresh_interval("balldontlie", [nearby], instant) == RESULT_REFRESH
    assert provider_refresh_interval("balldontlie", [far], instant) == PROVIDER_REFRESH
