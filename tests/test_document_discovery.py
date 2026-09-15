from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pydantic import SecretStr, ValidationError
import pytest

from app.config import settings
from app.document_accounts import Outbox, owner_partition
from app.document_discovery import DISCOVERY_PK, event_query
from app.document_runtime import Runtime
from app.document_store import LocalDocumentStore, partition_items
from app.document_worker import run_job
from app.schemas import Config, Preferences


def test_search_window_preferences_are_unique_and_bounded():
    assert Preferences().content_search_windows == ["before_24h", "after_3h"]
    with pytest.raises(ValidationError):
        Preferences(content_search_windows=["before_3h", "before_3h"])
    with pytest.raises(ValidationError):
        Preferences(content_search_windows=["before_24h", "before_3h", "after_3h"])


def test_event_query_is_deterministic_and_contains_no_web_fallback():
    event = SimpleNamespace(
        sport="basketball",
        title="Liverpool vs Tottenham",
        source_key="fixture:event",
        participants=[
            {"name": "Liverpool", "short_name": "LIV"},
            {"name": "Tottenham Hotspur", "short_name": "TOT"},
        ],
    )
    assert event_query(event, "after_3h") == (
        "Liverpool Tottenham Hotspur LIV TOT Liverpool vs Tottenham highlights analysis"
    )
    assert "site:" not in event_query(event, "after_3h")


def test_shared_search_fanout_adds_every_automatic_video(tmp_path, monkeypatch):
    store = LocalDocumentStore(tmp_path / "discovery.db")
    cfg = settings().model_copy(
        update={
            "storage_backend": "documents-local",
            "document_local_path": str(store.path),
            "youtube_project_id": "fixture-youtube-project",
            "youtube_daily_budget": 100,
            "matching_ai_enabled": True,
            "matching_ai_api_key": SecretStr("fixture-gemini-key"),
        }
    )
    monkeypatch.setenv("YOUTUBE_API_KEY", "fixture-youtube-key")
    rt = Runtime(store, cfg)
    instant = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
    starts = instant + timedelta(hours=24)
    event = {
        "id": "event-1",
        "source_key": "fixture:event:1",
        "competition_id": "fixture:league",
        "sport": "football",
        "title": "Liverpool vs Tottenham Hotspur",
        "starts_at": starts.isoformat(),
        "local_date": starts.date().isoformat(),
        "time_precision": "exact",
        "timezone": "UTC",
        "duration": 120,
        "venue": "",
        "status": "scheduled",
        "participants": [
            {"id": "fixture:liverpool", "name": "Liverpool", "short_name": "LIV", "color": "#f00"},
            {"id": "fixture:tottenham", "name": "Tottenham Hotspur", "short_name": "TOT", "color": "#fff"},
        ],
        "provider": "fixture",
        "source_url": "https://example.test/event",
        "updated_at": "2026-09-15T00:00:00+00:00",
        "demo": False,
    }
    sources = [
        {
            "id": "fixture:liverpool",
            "name": "Liverpool",
            "short_name": "LIV",
            "color": "#f00",
            "sport": "football",
            "kind": "team",
            "demo": False,
        }
    ]
    rt.catalog.publish("fixture", [event], sources, expected_revision=0, complete=True)
    for user_id in ["user-one", "user-two"]:
        account = rt.accounts.ensure(user_id)
        config = Config.model_validate(account["payload"]["config"]).model_dump()
        config["follows"] = [{"type": "team", "source_key": "fixture:liverpool"}]
        rt.accounts.save_config(user_id, config, account["payload"]["revision"])

    channel = "UC" + "a" * 22
    rt.discovery.set_official(
        channel,
        SimpleNamespace(
            official=True,
            evidence_url="https://example.test/official",
            valid_until="2027-09-15T00:00:00+00:00",
        ),
        "maintainer",
    )
    ids = [f"video{i:06d}" for i in range(5)]
    assert all(len(ident) == 11 for ident in ids)

    def youtube_request(endpoint, params):
        if endpoint == "search":
            assert params["type"] == "video" and params["maxResults"] == 25
            return {"items": [{"id": {"videoId": ident}} for ident in ids]}
        if endpoint == "videos":
            return {
                "items": [
                    {
                        "id": ident,
                        "snippet": {
                            "channelId": channel,
                            "channelTitle": "Official Fixture Channel",
                            "publishedAt": instant.isoformat(),
                            "title": f"Liverpool Tottenham match analysis {index}",
                            "description": "Liverpool against Tottenham Hotspur",
                        },
                        "status": {"privacyStatus": "public"},
                        "statistics": {"viewCount": str(20_000 + index)},
                    }
                    for index, ident in enumerate(ids)
                ]
            }
        assert endpoint == "commentThreads"
        return {"items": []}

    monkeypatch.setattr(rt, "youtube_request", youtube_request)
    monkeypatch.setattr(
        "app.document_discovery.evaluate_with_ai",
        lambda video, events, cfg, creator_name="": [
            {
                "event_id": events[0].id,
                "kind": "video",
                "content_labels": ["📊战术分析"],
                "decision": "automatic",
                "reason_codes": ["AI_CONFIDENCE_095", "BOTH_PARTICIPANTS"],
                "rule_version": "matching-ai-v4",
            }
        ],
    )

    assert rt.discovery.schedule(instant=instant) == 1
    assert rt.discovery.schedule(instant=instant) == 0
    search_job = next(
        row
        for row in partition_items(store, "state", DISCOVERY_PK, "outbox")
        if row["payload"]["operation"] == "event_search"
    )
    assert run_job(rt, {"version": 1, "pk": DISCOVERY_PK, "job_id": search_job["id"]})
    fanout_job = next(
        row
        for row in partition_items(store, "state", DISCOVERY_PK, "outbox")
        if row["payload"]["operation"] == "discovery_fanout"
    )
    claim = Outbox(store).claim(DISCOVERY_PK, fanout_job["id"])
    rt.discovery.fanout(claim)
    completed_fanout = store.get("state", DISCOVERY_PK, fanout_job["id"])
    assert completed_fanout["state"] == "done", completed_fanout["payload"]["error"]
    for user_id in ["user-one", "user-two"]:
        links = list(partition_items(store, "state", owner_partition(user_id), "link"))
        assert len(links) == 5
        assert all(row["payload"]["origin"] == "discovery" for row in links)
        assert all(
            row["payload"]["content_labels"] == ["✅官方频道", "🔥热门", "📊战术分析"]
            for row in links
        )
    first_links = list(partition_items(store, "state", owner_partition("user-one"), "link"))
    original_labels = {row["payload"]["id"]: row["payload"]["content_labels"] for row in first_links}
    rt.content.override_link("user-one", first_links[0]["payload"]["id"], "block", key="block-discovery-1")
    rt.content.override_link("user-one", first_links[1]["payload"]["id"], "pin", key="pin-discovery-1")
    active = rt.accounts.active("user-one")
    assessments = list(partition_items(store, "state", DISCOVERY_PK, "event_video_assessment"))
    for assessment in assessments:
        assessment["payload"]["display_labels"] = ["🧪更新标签"]
    rt.discovery._publish_owner(active, rt.catalog.capture().event("event-1"), assessments)
    retained = list(partition_items(store, "state", owner_partition("user-one"), "link"))
    by_id = {row["payload"]["id"]: row["payload"] for row in retained}
    assert by_id[first_links[0]["payload"]["id"]]["content_labels"] == original_labels[
        first_links[0]["payload"]["id"]
    ]
    assert by_id[first_links[1]["payload"]["id"]]["content_labels"] == original_labels[
        first_links[1]["payload"]["id"]
    ]
    feedback = list(partition_items(store, "state", owner_partition("user-one"), "channel_feedback"))
    assert len(feedback) == 1 and feedback[0]["payload"]["action"] == "removed"
    assert len(list(partition_items(store, "state", DISCOVERY_PK, "event_search_run"))) == 1
    store.close()
