"""Synthetic, isolated fixtures prove local boundaries, not YouTube or device acceptance."""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func

import app.content as content
import app.websub as websub
from app.calendar import event_view, rebuild_feed
from app.config import settings
from app.db import ChannelSync, Creator, Event, Feed, Job, NotificationReceipt, Source, User, Video, VideoMatch
from app.matching import evaluate
from app.schemas import CreatorFollow
from app.service import ensure_user, save_config
from app.worker import run_one

CHANNEL = "UC" + "a" * 22
VID = "abcdefghijk"


def setup_content(sessions, both=True):
    start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        creator = Creator(channel_id=CHANNEL, name="Fixture creator", uploads_id="UUfixture")
        event = Event(
            source_key="fixture:game",
            competition_id="fixture:nba",
            sport="basketball",
            title="Lakers vs Warriors",
            starts_at=start.isoformat(),
            timezone="UTC",
            duration=150,
            provider="fixture",
            demo=True,
            participants=[
                {"id": "fixture:LAL", "name": "Lakers", "short_name": "LAL", "color": "#fff"},
                {"id": "fixture:GSW", "name": "Warriors", "short_name": "GSW", "color": "#fff"},
            ],
        )
        source = Source(
            id="fixture:LAL",
            name="Lakers",
            short_name="LAL",
            sport="basketball",
            kind="team",
            color="#fff",
            provider="fixture",
            demo=True,
        )
        db.add_all([creator, event, source])
        db.flush()
        save_config(
            db,
            user,
            {
                **user.config,
                "follows": [{"type": "team", "source_key": "fixture:LAL"}],
                "creators": [
                    CreatorFollow(channel_id=CHANNEL, scope_keys=["fixture:LAL"]).model_dump()
                ],
                "event_overrides": [{"event_key": event.source_key, "state": "include"}],
            },
            user.revision,
        )
        video = Video(
            id=VID,
            channel_id=CHANNEL,
            title=f"Lakers {'Warriors' if both else ''} {start.date()} preview",
            published_at=datetime.now(timezone.utc).isoformat(),
            description="",
        )
        db.add_all([video, ChannelSync(channel_id=CHANNEL)])
        db.flush()
        content.match_video(db, video)
        rebuild_feed(db, user.id)
        db.commit()
        return event.id


def api_video(title, channel=CHANNEL, privacy="public"):
    return {
        "id": VID,
        "snippet": {
            "channelId": channel,
            "title": title,
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "description": "",
        },
        "status": {"privacyStatus": privacy},
    }


def test_auto_is_idempotent_and_contradictory_title_withdraws(stack, monkeypatch):
    _, sessions = stack
    ident = setup_content(sessions)
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        event = db.get(Event, ident)
        video = db.get(Video, VID)
        assert len(event_view(db, event, user)["links"]) == 1
        feed = db.scalar(select(Feed))
        before = (feed.revision, feed.etag, feed.updated_at)
        content.match_video(db, video)
        rebuild_feed(db, user.id)
        assert (feed.revision, feed.etag, feed.updated_at) == before
        monkeypatch.setattr(
            content, "youtube_request", lambda *args: {"items": [api_video("NBA trade news")]}
        )
        content.refresh_videos(db, CHANNEL, [VID])
        rebuild_feed(db, user.id)
        assert event_view(db, event, user)["links"] == []
        content.refresh_videos(db, CHANNEL, [VID])
        assert event_view(db, event, user)["links"] == []


def test_block_survives_refresh_and_user_isolation(stack, monkeypatch):
    client, sessions = stack
    ident = setup_content(sessions)
    link = client.get(f"/api/v1/events/{ident}").json()["links"][0]
    assert client.post(f"/api/v1/me/links/{link['id']}/block").status_code == 200
    with sessions() as db:
        video = db.get(Video, VID)
        monkeypatch.setattr(content, "youtube_request", lambda *args: {"items": [api_video(video.title)]})
        content.refresh_videos(db, CHANNEL, [VID])
        db.commit()
        other = ensure_user(db, "other")
        db.commit()
        assert event_view(db, db.get(Event, ident), other)["links"] == []
    assert client.get(f"/api/v1/events/{ident}").json()["links"] == []


@pytest.mark.parametrize("choice", ["automatic", "pin", "block"])
def test_rule_upgrade_withdraws_description_only_auto_link_but_keeps_personal_choices(stack, choice):
    from icalendar import Calendar

    client, sessions = stack
    ident = setup_content(sessions)
    link = client.get(f"/api/v1/events/{ident}").json()["links"][0]
    if choice != "automatic":
        assert client.post(f"/api/v1/me/links/{link['id']}/{choice}").status_code == 200
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        video = db.get(Video, VID)
        video.description, video.title = video.title, "A day in my life"
        match = db.scalar(select(VideoMatch).where(VideoMatch.video_id == VID))
        match.rule_version = "matching-v1"  # Existing stored association from the old rules.
        rebuild_feed(db, user.id)
        feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
        before = Calendar.from_ical(feed.body).walk("VEVENT")[0]
        old_revision = feed.revision
        content.match_video(db, video)
        rebuild_feed(db, user.id)
        after = Calendar.from_ical(feed.body).walk("VEVENT")[0]
        assert str(before["UID"]) == str(after["UID"])
        visible = event_view(db, db.get(Event, ident), user)["links"]
        assert bool(visible) == (choice == "pin")
        if choice == "automatic":
            assert feed.revision == old_revision + 1
            assert int(after["SEQUENCE"]) == int(before["SEQUENCE"]) + 1
            assert match.rule_version == "matching-v4"
            assert match.decision == "needs_review"
        elif choice == "block":
            assert match.decision == "ignored"
        stable = (feed.body, feed.etag, feed.revision, feed.updated_at)
        content.match_video(db, video)
        rebuild_feed(db, user.id)
        assert (feed.body, feed.etag, feed.revision, feed.updated_at) == stable


def test_pause_preserves_existing_and_delete_preserves_pin(stack):
    client, sessions = stack
    ident = setup_content(sessions)
    user = client.get("/api/v1/me/calendar").json()
    channel = user["creators"][0]
    payload = {k: channel[k] for k in ["scope_keys", "preview", "recap", "enabled"]}
    payload.update(enabled=False, expected_revision=user["revision"])
    assert client.patch(f"/api/v1/me/creators/{CHANNEL}", json=payload).status_code == 200
    links = client.get(f"/api/v1/events/{ident}").json()["links"]
    assert len(links) == 1
    assert client.post(f"/api/v1/me/links/{links[0]['id']}/pin").status_code == 200
    impact = client.get(f"/api/v1/me/creators/{CHANNEL}/impact").json()
    assert impact["automatic_removed"] == 0 and impact["manual_retained"] == 1
    path = f"/api/v1/me/creators/{CHANNEL}?expected_revision={impact['revision']}"
    assert client.delete(path).status_code == 400
    assert client.delete(path + "&confirmed=true").status_code == 200
    assert client.get(f"/api/v1/events/{ident}").json()["links"][0]["pinned"]


def test_review_confirm_checks_owner_and_version_then_retains_manual_choice(stack):
    client, sessions = stack
    ident = setup_content(sessions, both=False)
    row = client.get("/api/v1/me/reviews").json()["items"][0]
    payload = {"decision": "confirm", "kind": "preview", "expected_updated_at": "stale"}
    assert client.post(f"/api/v1/me/reviews/{row['id']}", json=payload).status_code == 409
    with sessions() as db:
        other = ensure_user(db, "other")
        with pytest.raises(HTTPException) as exc:
            content.decide_review(db, other, row["id"], "confirm", "preview", row["updated_at"])
        assert exc.value.status_code == 404
        db.rollback()
    payload["expected_updated_at"] = row["updated_at"]
    assert client.post(f"/api/v1/me/reviews/{row['id']}", json=payload).status_code == 200
    with sessions() as db:
        video = db.get(Video, VID)
        video.title = "Different topic"
        content.match_video(db, video)
        db.commit()
    link = client.get(f"/api/v1/events/{ident}").json()["links"][0]
    assert link["origin"] == "confirmed" and link["pinned"]
    assert client.get("/api/v1/me/reviews").json()["items"] == []


def test_ignore_and_scope_exclusion_do_not_auto_restore(stack):
    client, sessions = stack
    ident = setup_content(sessions, both=False)
    row = client.get("/api/v1/me/reviews").json()["items"][0]
    assert (
        client.post(
            f"/api/v1/me/reviews/{row['id']}",
            json={"decision": "ignore", "kind": "preview", "expected_updated_at": row["updated_at"]},
        ).status_code
        == 200
    )
    with sessions() as db:
        video = db.get(Video, VID)
        event = db.get(Event, ident)
        video.title = f"Lakers Warriors {event.starts_at[:10]} preview"
        content.match_video(db, video)
        db.commit()
        assert event_view(db, event, db.get(User, "local-reviewer"))["links"] == []


def test_api_channel_spoof_rolls_back_and_private_video_is_removed(stack, monkeypatch):
    client, sessions = stack
    ident = setup_content(sessions)
    monkeypatch.setattr(
        content,
        "youtube_request",
        lambda *args: {"items": [api_video("wrong channel", channel="UC" + "b" * 22)]},
    )
    with sessions() as db:
        with pytest.raises(ValueError, match="CHANNEL_ID_MISMATCH"):
            content.refresh_videos(db, CHANNEL, [VID])
        db.rollback()
    assert len(client.get(f"/api/v1/events/{ident}").json()["links"]) == 1
    monkeypatch.setattr(content, "youtube_request", lambda *args: {"items": []})
    with sessions() as db:
        content.refresh_videos(db, CHANNEL, [VID])
        db.commit()
        assert not db.get(Video, VID).available
    assert client.get(f"/api/v1/events/{ident}").json()["links"] == []


def test_comment_sample_is_stored_and_transient_comment_failure_keeps_previous_sample(
    stack, monkeypatch
):
    _, sessions = stack
    setup_content(sessions)
    calls = []

    def request(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "videos":
            return {"items": [api_video("Lakers Warriors reaction")]}
        assert endpoint == "commentThreads"
        return {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "snippet": {"textDisplay": "Warriors struggled against the Lakers defense"}
                        }
                    }
                }
            ]
        }

    monkeypatch.setattr(content, "youtube_request", request)
    with sessions() as db:
        content.refresh_videos(db, CHANNEL, [VID])
        assert db.get(Video, VID).comments == ["Warriors struggled against the Lakers defense"]
        db.commit()
    assert calls[1] == (
        "commentThreads",
        {
            "videoId": VID,
            "part": "snippet",
            "maxResults": 12,
            "order": "relevance",
            "textFormat": "plainText",
        },
    )

    def comments_fail(endpoint, params):
        if endpoint == "videos":
            return {"items": [api_video("Lakers Warriors updated reaction")]}
        raise HTTPException(503, {"code": "YOUTUBE_API_UNAVAILABLE"})

    monkeypatch.setattr(content, "youtube_request", comments_fail)
    with sessions() as db:
        content.refresh_videos(db, CHANNEL, [VID])
        assert db.get(Video, VID).comments == ["Warriors struggled against the Lakers defense"]


def test_pagination_does_not_treat_absence_as_deletion(stack, monkeypatch):
    _, sessions = stack
    setup_content(sessions)

    def request(endpoint, args):
        if endpoint == "playlistItems":
            return {"items": [], "nextPageToken": "page2"}
        raise AssertionError("unexpected API call")

    monkeypatch.setattr(content, "youtube_request", request)
    with sessions() as db:
        content.poll_channel(db, {"channel_id": CHANNEL})
        assert db.get(Video, VID).available
        assert db.get(ChannelSync, CHANNEL).last_success is None
        continuation = db.scalar(select(Job).where(Job.kind == "youtube_poll"))
        assert continuation.payload["cursor"] == "page2"
        with pytest.raises(ValueError, match="PAGINATION_LOOP"):
            content.poll_channel(db, {"channel_id": CHANNEL, "cursor": "page2", "seen": ["page2"]})


def test_expired_metadata_is_not_renewed_by_preference_edits(stack):
    client, sessions = stack
    ident = setup_content(sessions)
    past = (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()
    with sessions() as db:
        db.get(Creator, CHANNEL).updated_at = past
        db.get(Video, VID).updated_at = past
        db.get(Video, VID).comments = ["stale public comment"]
        db.commit()
    user = client.get("/api/v1/me/calendar").json()
    follow = user["creators"][0]
    payload = {k: follow[k] for k in ["scope_keys", "preview", "recap", "enabled"]}
    payload.update(expected_revision=user["revision"], enabled=False)
    assert client.patch(f"/api/v1/me/creators/{CHANNEL}", json=payload).status_code == 200
    with sessions() as db:
        assert db.get(Creator, CHANNEL).updated_at == past
        content.expire_metadata(db)
        db.commit()
        assert db.get(Video, VID).title == "元数据已过期"
        assert db.get(Video, VID).comments == []
        assert db.get(Creator, CHANNEL).name == CHANNEL
    assert client.get(f"/api/v1/events/{ident}").json()["links"] == []


def prepare_hook(sessions):
    setup_content(sessions)
    with sessions() as db:
        sync = db.get(ChannelSync, CHANNEL)
        sync.secret_ciphertext = settings().cipher().encrypt(b"fixture-secret").decode()
        sync.state = "pending"
        sync.pending_until = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        db.commit()
        return sync.callback_id


def hook_body(channel=CHANNEL):
    return f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>{VID}</yt:videoId><yt:channelId>{channel}</yt:channelId></entry></feed>'.encode()


def sign(body):
    return "sha1=" + hmac.new(b"fixture-secret", body, hashlib.sha1).hexdigest()


def test_signed_notification_intent_lease_duplicate_and_forgery(stack):
    client, sessions = stack
    callback = prepare_hook(sessions)
    path = "/webhooks/youtube/" + callback
    params = {
        "hub.mode": "subscribe",
        "hub.topic": websub.topic(CHANNEL),
        "hub.challenge": "fixture-challenge",
        "hub.lease_seconds": "1000",
    }
    assert client.get(path, params={**params, "hub.topic": websub.topic("foreign")}).status_code == 404
    start = datetime.now(timezone.utc)
    response = client.get(path, params=params)
    assert response.text == "fixture-challenge"
    with sessions() as db:
        sync = db.get(ChannelSync, CHANNEL)
        assert 799 <= (datetime.fromisoformat(sync.renew_at) - start).total_seconds() <= 801
    body = hook_body()
    for _ in range(2):
        assert client.post(path, content=body, headers={"X-Hub-Signature": sign(body)}).status_code == 204
    bad = hook_body("foreign")
    assert client.post(path, content=bad, headers={"X-Hub-Signature": sign(bad)}).status_code == 204
    assert client.post(path, content=body, headers={"X-Hub-Signature": "sha1=" + "0" * 40}).status_code == 204
    xml = b'<!DOCTYPE feed [<!ENTITY exploit SYSTEM "file:///not-accessible">]><feed>&exploit;</feed>'
    assert client.post(path, content=xml, headers={"X-Hub-Signature": sign(xml)}).status_code == 204
    assert client.post(path, content=b"x" * 65537).status_code == 413
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(NotificationReceipt)) == 1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "youtube_videos")) == 1


def test_unsubscribe_challenge_after_last_follower_is_removed(stack):
    client, sessions = stack
    callback = prepare_hook(sessions)
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        save_config(db, user, {**user.config, "creators": []}, user.revision)
        db.get(ChannelSync, CHANNEL).state = "unsubscribing"
        db.commit()
    response = client.get(
        "/webhooks/youtube/" + callback,
        params={"hub.mode": "unsubscribe", "hub.topic": websub.topic(CHANNEL), "hub.challenge": "bye"},
    )
    assert response.text == "bye"
    with sessions() as db:
        assert db.get(ChannelSync, CHANNEL).state == "unsubscribed"


def test_missing_key_is_actionable_and_shared_poll_is_deduplicated(stack, monkeypatch):
    client, sessions = stack
    setup_content(sessions)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    assert client.post("/api/v1/me/creators/resolve", json={"url": "@fixture"}).status_code == 503
    with sessions() as db:
        content.enqueue_channel(db, CHANNEL)
        content.enqueue_channel(db, CHANNEL)
        db.commit()
        ids = list(db.scalars(select(Job.id).where(Job.kind == "youtube_poll")))
        assert len(ids) == 1
    assert run_one(ids[0])
    with sessions() as db:
        job = db.get(Job, ids[0])
        assert job.state == "failed" and job.error == "YOUTUBE_KEY_REQUIRED"


@pytest.mark.parametrize(
    "suffix,expected",
    [(" preview", "automatic"), (" trade preview", "reject"), (" highlights", "needs_review")],
)
def test_match_requires_phase_and_rejects_unrelated_topics(stack, suffix, expected):
    _, sessions = stack
    ident = setup_content(sessions)
    with sessions() as db:
        event = db.get(Event, ident)
        video = db.get(Video, VID)
        video.title = f"Lakers Warriors {event.starts_at[:10]}" + suffix
        assert evaluate(video, [event])[0]["decision"] == expected


def test_same_day_doubleheader_and_f1_unspecified_session_require_review(stack):
    _, sessions = stack
    ident = setup_content(sessions)
    with sessions() as db:
        event = db.get(Event, ident)
        video = db.get(Video, VID)
        duplicate = SimpleNamespace(
            **{
                k: getattr(event, k)
                for k in [
                    "sport",
                    "title",
                    "starts_at",
                    "timezone",
                    "duration",
                    "status",
                    "participants",
                    "source_key",
                ]
            },
            id="second",
        )
        assert all(x["decision"] == "needs_review" for x in evaluate(video, [event, duplicate]))
        event.sport = "racing"
        event.title = "Italian Grand Prix · Qualifying"
        event.source_key = "2026:monza:Qualifying"
        video.title = "Monza preview"
        assert evaluate(video, [event])[0]["decision"] == "needs_review"
        video.title = "Monza qualifying preview"
        assert evaluate(video, [event])[0]["decision"] == "automatic"


def test_hub_intent_is_committed_before_verification_request(stack, monkeypatch):
    client, sessions = stack
    setup_content(sessions)
    monkeypatch.setattr(websub, "SessionLocal", sessions)
    cfg = SimpleNamespace(
        youtube_websub_enabled=True, public_url="https://fixture.example", cipher=settings().cipher
    )
    monkeypatch.setattr(websub, "settings", lambda: cfg)

    class HubClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, data):
            assert url == websub.HUB and data["hub.mode"] == "subscribe"
            with sessions() as db:
                sync = db.get(ChannelSync, CHANNEL)
                assert sync.state == "pending" and sync.pending_until
                callback = sync.callback_id
            response = client.get(
                "/webhooks/youtube/" + callback,
                params={
                    "hub.mode": "subscribe",
                    "hub.topic": data["hub.topic"],
                    "hub.challenge": "verified",
                    "hub.lease_seconds": "432000",
                },
            )
            assert response.text == "verified"
            return SimpleNamespace(status_code=202)

    monkeypatch.setattr(websub.httpx, "Client", HubClient)
    from app.jobs import claim_job

    with sessions() as db:
        job = Job(kind="youtube_subscribe", payload={"channel_id": CHANNEL})
        db.add(job)
        db.flush()
        claim, _ = claim_job(db, job.id)
        db.commit()
    websub.request_subscription(CHANNEL, claim)
    with sessions() as db:
        assert db.get(ChannelSync, CHANNEL).state == "verified"


def test_shared_scheduler_enqueues_one_poll_and_one_renewal(stack, monkeypatch):
    _, sessions = stack
    setup_content(sessions)
    with sessions() as db:
        other = ensure_user(db, "second-user")
        save_config(
            db,
            other,
            {**other.config, "creators": [CreatorFollow(channel_id=CHANNEL).model_dump()]},
            other.revision,
        )
        db.commit()
    monkeypatch.setattr(websub, "SessionLocal", sessions)
    monkeypatch.setattr(websub, "settings", lambda: SimpleNamespace(youtube_websub_enabled=True))
    websub.schedule_content()
    websub.schedule_content()
    with sessions() as db:
        for kind in ["youtube_poll", "youtube_subscribe"]:
            assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == kind)) == 1
