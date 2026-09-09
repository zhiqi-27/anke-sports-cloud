from datetime import datetime, timedelta, timezone

from icalendar import Calendar
from sqlalchemy import func, select

from app import public_feeds
from app.config import settings
from app.db import Event, Feed, Job, Link, Projection, PublicFeed, Source, User, now
from app.security import digest
from app.providers import sync_provider
from app.service import attach_link, save_config
from app.worker import run_one
from tests.test_broadcasts import payload, publish
from tests.test_calendar_flow import drain, feed_snapshot, insert_event


def setup_public(stack):
    client, sessions = stack
    with sessions() as db:
        for ident, kind in [("test:league", "competition"), ("test:team", "team"), ("test:other", "team")]:
            db.add(
                Source(
                    id=ident,
                    name="测试 " + ident,
                    short_name="TEST",
                    kind=kind,
                    sport="basketball",
                    color="#123456",
                    provider="test",
                    demo=True,
                )
            )
        db.commit()
    event_id = insert_event(sessions)
    with sessions() as db:
        db.get(Event, event_id).participants = [{"id": "test:team"}]
        public_feeds.enqueue_public_feeds(db)
        db.commit()
    drain()
    return event_id, info(client)


def info(client, key="test:league"):
    response = client.get("/api/v1/public-feed", params={"source_key": key})
    response.raise_for_status()
    return response.json()


def entries(response):
    response.raise_for_status()
    return Calendar.from_ical(response.content).walk("VEVENT")


def test_anonymous_snapshot_reads_are_pure_and_conditional(stack):
    client, sessions = stack
    _, view = setup_public(stack)
    private_url, private, _ = feed_snapshot(client)
    with sessions() as db:
        baseline = {
            table.name: db.execute(select(table)).all() for table in PublicFeed.metadata.sorted_tables
        }
    client.cookies.clear()
    assert client.get("/api/v1/me/feed/address").status_code == 401
    read = client.get(view["url"])
    assert info(client) == view
    assert len(entries(read)) == view["event_count"] == 1
    assert read.headers["cache-control"] == "public, max-age=0, must-revalidate"
    assert "attachment" in read.headers["content-disposition"]
    assert "charset=utf-8" in read.headers["content-type"]
    assert "private" in private.headers["cache-control"]
    assert client.get(view["url"], headers={"If-None-Match": "W/" + read.headers["etag"]}).status_code == 304
    assert (
        client.get(view["url"], headers={"If-Modified-Since": read.headers["last-modified"]}).status_code
        == 304
    )
    assert (
        client.get(
            view["url"],
            headers={"If-None-Match": '"different"', "If-Modified-Since": read.headers["last-modified"]},
        ).status_code
        == 200
    )
    assert client.get(view["url"], headers={"If-Modified-Since": "invalid"}).status_code == 200
    head = client.head(view["url"])
    assert head.content == b"" and head.headers["content-length"] == str(len(read.content))
    assert client.get("/public-feeds/" + private_url.split("/")[-1]).status_code == 404
    assert client.get("/feeds/" + view["url"].split("/")[-1]).status_code == 404
    with sessions() as db:
        assert {
            table.name: db.execute(select(table)).all() for table in PublicFeed.metadata.sorted_tables
        } == baseline


def test_sources_and_personal_links_are_isolated_with_distinct_uids(stack):
    client, sessions = stack
    ident, view = setup_public(stack)
    public = client.get(view["url"])
    _, _, personal = feed_snapshot(client)
    assert str(entries(public)[0]["UID"]) != str(personal[0]["UID"])
    assert entries(client.get(info(client, "test:other")["url"])) == []
    assert len(entries(client.get(info(client, "test:team")["url"]))) == 1
    with sessions() as db:
        user, event = db.get(User, "local-reviewer"), db.get(Event, ident)
        attach_link(db, user, event, "https://youtu.be/abcdefghijk", "PRIVATE_CREATOR", "preview")
        # Legacy/unreviewed public rows are not publication authority.
        db.add(
            Link(
                owner_id="public",
                event_id=ident,
                url="https://youtu.be/zyxwvutsrqp",
                url_hash=digest("unreviewed"),
                title="UNREVIEWED",
                kind="live",
                platform="YouTube",
            )
        )
        public_feeds.enqueue_public_feeds(db, force=True)
        db.commit()
    drain()
    assert client.get(view["url"]).content == public.content
    assert "PRIVATE_CREATOR" not in public.text and "UNREVIEWED" not in public.text
    assert "演示赛程" in str(entries(public)[0]["DESCRIPTION"])


def test_reschedule_date_only_cancellation_and_noop_keep_identity(stack):
    client, sessions = stack
    ident, view = setup_public(stack)
    first = entries(client.get(view["url"]))[0]
    uid, sequence = str(first["UID"]), int(first["SEQUENCE"])
    with sessions() as db:
        event = db.get(Event, ident)
        event.starts_at = (datetime.fromisoformat(event.starts_at) + timedelta(hours=2)).isoformat()
        public_feeds.enqueue_public_feeds(db, force=True)
        db.commit()
    # Schedule writes do not mutate a previously published HTTP response.
    assert int(entries(client.get(view["url"]))[0]["SEQUENCE"]) == sequence
    drain()
    for expected, change in [(sequence + 2, "date"), (sequence + 3, "cancel")]:
        with sessions() as db:
            event = db.get(Event, ident)
            if change == "date":
                event.time_precision, event.starts_at = "date_only", None
            else:
                event.status = "cancelled"
            public_feeds.enqueue_public_feeds(db, force=True)
            db.commit()
        drain()
        event = entries(client.get(view["url"]))[0]
        assert str(event["UID"]) == uid and int(event["SEQUENCE"]) == expected
        assert event["DTSTART"].params["VALUE"] == "DATE"
    assert str(event["STATUS"]) == "CANCELLED"
    before = client.get(view["url"])
    before_info = info(client)
    with sessions() as db:
        public_feeds.enqueue_public_feeds(db, force=True)
        db.commit()
    drain()
    assert client.get(view["url"]).content == before.content
    assert info(client) == before_info


def test_broadcast_publishes_and_withdraws_from_public_original_event(stack, monkeypatch):
    client, sessions = stack
    ident, view = setup_public(stack)
    monkeypatch.setattr(settings(), "maintainer_ids", ["local-reviewer"])
    before = client.get(view["url"])
    record = client.post("/api/v1/maintenance/broadcasts", json=payload(ident)).json()
    assert client.get(view["url"]).content == before.content
    published = publish(client, record)
    published.raise_for_status()
    drain()
    event = entries(client.get(view["url"]))[0]
    assert "abcdefghijk" in str(event["DESCRIPTION"]) and "仅限 US" in str(event["DESCRIPTION"])
    assert str(event["UID"]) == str(entries(before)[0]["UID"])
    assert int(event["SEQUENCE"]) == int(entries(before)[0]["SEQUENCE"]) + 1
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        save_config(
            db,
            user,
            {
                **user.config,
                "link_overrides": [
                    {
                        "event_key": db.get(Event, ident).source_key,
                        "url": record["draft"]["url"],
                        "state": "block",
                    }
                ],
            },
            user.revision,
        )
        public_feeds.enqueue_public_feeds(db, force=True)
        db.commit()
    drain()
    assert "abcdefghijk" in str(entries(client.get(view["url"]))[0]["DESCRIPTION"])
    response = client.post(
        f"/api/v1/maintenance/broadcasts/{record['id']}/suspend",
        json={"expected_revision": published.json()["revision"], "reason": "合成公共订阅撤回验收"},
    )
    response.raise_for_status()
    drain()
    after = entries(client.get(view["url"]))[0]
    assert "abcdefghijk" not in str(after["DESCRIPTION"])
    assert str(after["UID"]) == str(event["UID"]) and str(after["STATUS"]) == "CONFIRMED"


def test_publishing_failure_rolls_back_and_keeps_last_snapshot(stack, monkeypatch):
    client, sessions = stack
    ident, view = setup_public(stack)
    before = client.get(view["url"])
    with sessions() as db:
        db.get(Event, ident).title = "修改后的合成比赛"
        public_feeds.enqueue_public_feeds(db, ["test:league"], force=True)
        db.commit()
    real_publish = public_feeds.publish_snapshot

    def fail_after_flush(*args):
        real_publish(*args)
        raise RuntimeError("FAILED_PUBLICATION")

    monkeypatch.setattr(public_feeds, "publish_snapshot", fail_after_flush)
    assert run_one()
    assert client.get(view["url"]).content == before.content
    with sessions() as db:
        job = db.scalar(select(Job).where(Job.state == "pending"))
        assert job.attempts == 1
        job.due_at = now()
        db.commit()
    monkeypatch.setattr(public_feeds, "publish_snapshot", real_publish)
    drain()
    after = entries(client.get(view["url"]))[0]
    assert str(after["SUMMARY"]) == "修改后的合成比赛"
    assert int(after["SEQUENCE"]) == int(entries(before)[0]["SEQUENCE"]) + 1


def test_deployed_public_distribution_is_explicit_and_demo_is_blocked(stack, monkeypatch):
    client, sessions = stack
    _, view = setup_public(stack)
    monkeypatch.setattr(settings(), "env", "staging")
    monkeypatch.setattr(settings(), "public_feed_source_keys", ["test:league"])
    assert info(client)["status"] == "unavailable"
    assert client.get(view["url"]).status_code == 404  # Even allowlisting cannot publish demo.
    with sessions() as db:
        db.get(Source, "test:league").demo = False
        db.commit()
    assert info(client)["status"] == "published"
    monkeypatch.setattr(settings(), "public_feed_source_keys", [])
    assert info(client)["url"] is None and client.get(view["url"]).status_code == 404


def test_empty_unbuilt_sources_do_not_create_users_or_work_on_get(stack):
    client, sessions = stack
    with sessions() as db:
        db.add(
            Source(
                id="empty",
                name="暂无赛程",
                short_name="空",
                kind="team",
                sport="football",
                color="#123456",
                provider="test",
                demo=True,
            )
        )
        db.commit()
    client.cookies.clear()
    for _ in range(3):
        view = info(client, "empty")
        assert view["status"] == "pending" and view["url"] is None
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(PublicFeed)) == 0
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(Feed)) == 1
        assert db.scalar(select(Job.id).where(Job.state == "pending")) is None
        public_feeds.enqueue_public_feeds(db)
        db.commit()
    drain()
    assert entries(client.get(info(client, "empty")["url"])) == []


def test_provider_change_and_outbox_commit_together(stack, monkeypatch):
    client, sessions = stack
    race = {
        "raceName": "合成 F1 赛程",
        "date": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(),
        "time": "12:00:00Z",
        "Circuit": {"circuitId": "synthetic", "circuitName": "合成赛道"},
    }
    response = {"MRData": {"total": "1", "RaceTable": {"Races": [race]}}}
    monkeypatch.setattr("app.providers.get_json", lambda *args, **kwargs: response)
    with sessions() as db:
        sync_provider(db, "jolpica")
        assert db.scalar(select(Job.id).where(Job.kind == "public_projection"))
        db.rollback()
    with sessions() as db:
        assert db.scalar(select(Source.id).where(Source.provider == "jolpica")) is None
        assert db.scalar(select(PublicFeed.id)) is None
        assert db.scalar(select(Job.id).where(Job.kind == "public_projection")) is None
        sync_provider(db, "jolpica")
        db.commit()
    drain()
    view = info(client, "jolpica:f1")
    before = entries(client.get(view["url"]))[0]
    race["time"] = "14:00:00Z"
    with sessions() as db:
        sync_provider(db, "jolpica")
        db.commit()
    assert entries(client.get(view["url"]))[0]["DTSTART"].dt == before["DTSTART"].dt
    drain()
    after = entries(client.get(view["url"]))[0]
    assert str(after["UID"]) == str(before["UID"])
    assert after["DTSTART"].dt - before["DTSTART"].dt == timedelta(hours=2)
    assert int(after["SEQUENCE"]) == int(before["SEQUENCE"]) + 1


def test_daily_window_maintenance_deduplicates_and_tombstones(stack):
    client, sessions = stack
    ident, view = setup_public(stack)
    with sessions() as db:
        public_feeds.enqueue_public_feeds(db)
        public_feeds.enqueue_public_feeds(db)
        assert db.scalar(select(Job.id).where(Job.state == "pending")) is None
        # Simulate crossing the retention boundary without waiting 90 days.
        event = db.get(Event, ident)
        event.starts_at = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        for feed in db.scalars(select(PublicFeed)):
            feed.scheduled_on = "2000-01-01"
        public_feeds.enqueue_public_feeds(db)
        db.commit()
    before = entries(client.get(view["url"]))[0]
    drain()
    after = entries(client.get(view["url"]))[0]
    assert str(after["STATUS"]) == "CANCELLED" and str(after["UID"]) == str(before["UID"])
    assert info(client)["event_count"] == 0
    with sessions() as db:
        row = db.scalar(select(Projection).where(Projection.feed_id == view["url"].split("/")[-1][:-4]))
        row.updated_at = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        public_feeds.enqueue_public_feeds(db, force=True)
        db.commit()
    drain()
    assert entries(client.get(view["url"])) == []
