from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.calendar_rules import calendar_membership, delivery_links
from app.link_rules import selected_links
from app.schemas import AddLink, Config, Preferences
from tests.test_calendar_flow import insert_event


def link(kind, url, *, ident):
    return SimpleNamespace(
        id=ident,
        owner_id="local-reviewer",
        event_id="event-one",
        url=url,
        title=kind,
        kind=kind,
        content_labels=["🎬旧视频"],
        platform="YouTube" if kind == "video" else "Web",
        creator="Fixture",
        channel_id="UC" + "a" * 22,
        origin="manual",
        access="unknown",
        regions=[],
        available=True,
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_manual_link_has_no_user_selectable_content_kind():
    for kind in ("video", "preview", "recap", "live", "watch_along"):
        with pytest.raises(ValidationError):
            AddLink(url="https://www.youtube.com/watch?v=abcdefghijk", kind=kind)
    assert AddLink(url="https://www.nba.com/game/example").model_dump() == {
        "url": "https://www.nba.com/game/example",
        "title": "",
    }


def test_legacy_video_search_setting_is_rejected_by_the_current_contract():
    with pytest.raises(ValidationError):
        Preferences.model_validate(
            {"timezone": "UTC", "content_search_windows": ["before_24h", "after_3h"]}
        )


def test_legacy_creator_settings_are_rejected_by_the_current_contract():
    with pytest.raises(ValidationError):
        Config.model_validate(
            {"creators": [{"channel_id": "legacy-channel", "scope_keys": ["fixture:team"]}]}
        )


def test_manual_event_source_is_independent_and_explicit():
    config = Config.model_validate({"manual_events": [{"event_id": "event-one"}]})
    assert config.manual_events[0].event_id == "event-one"
    assert "manual_events" in config.model_dump()
    event = SimpleNamespace(
        id="event-one",
        source_key="provider:event-one",
        competition_id="provider:league",
        participants=[{"id": "provider:team-one"}],
    )
    membership = calendar_membership(event, config.model_dump())
    assert membership == {
        "sources": [{"type": "manual", "key": "event-one", "name": "手动添加"}],
        "can_remove": True,
    }
    covered = calendar_membership(
        event,
        {
            **config.model_dump(),
            "follows": [{"type": "team", "source_key": "provider:team-one"}],
        },
    )
    assert [source["type"] for source in covered["sources"]] == ["follow", "manual"]
    assert covered["can_remove"] is False


def test_historical_video_rows_never_enter_event_or_calendar_delivery():
    event = SimpleNamespace(
        source_key="provider:event", competition_id="provider:competition", status="scheduled"
    )
    config = {"manual_events": [], "link_overrides": [], "preferences": {}}
    video = link("video", "https://www.youtube.com/watch?v=abcdefghijk", ident="video")
    live = link("live", "https://example.com/live", ident="live")
    selected = selected_links(event, config, [video, live], lambda _: None, "local-reviewer")
    assert [row["id"] for row in selected] == ["live"]
    assert [row["id"] for row in delivery_links([*selected, {**selected[0], "id": "other"}])] == [
        "live"
    ]


def test_public_api_has_no_video_surfaces(stack):
    client, sessions = stack
    ident = insert_event(sessions)
    event = client.get(f"/api/v1/events/{ident}").json()
    assert event["calendar"] == {
        "sources": [{"type": "manual", "key": ident, "name": "手动添加"}],
        "can_remove": True,
    }
    response = client.post(
        f"/api/v1/events/{ident}/links",
        json={"url": "https://www.youtube.com/watch?v=abcdefghijk", "kind": "video"},
    )
    assert response.status_code == 422
    for path in ("/api/v1/me/creators", "/api/v1/me/reviews", "/webhooks/youtube/retired"):
        assert client.get(path).status_code == 404
    status = client.get("/api/v1/status").json()
    assert "youtube_budget" not in status
    assert not any("youtube" in key for key in status["integrations"])
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(
        "creators" in path or "reviews" in path or "youtube" in path or "selection" in path
        for path in paths
    )
    config_schema = client.get("/openapi.json").json()["components"]["schemas"]["Config-Output"]
    assert "manual_events" in config_schema["properties"]
    assert "event_overrides" not in config_schema["properties"]
