"""Selection parity, pending publication coalescing and old association retirement."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from sqlalchemy import delete, event as sql_event, func, select

from app.calendar import event_is_past, included, select_feed_events
from app.db import Event, Feed, Job, Link, Projection, User, Video, VideoMatch
from app.service import enqueue, ensure_user, save_config


def test_feed_candidates_keep_original_selection_and_bound_object_loads(stack):
    _, sessions = stack
    instant = datetime.now(timezone.utc)
    key = 'fixture:球队"_%\\'
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
        for i in range(600):
            db.add(
                Event(
                    id=f"matrix-{i}",
                    source_key=f"matrix:game:{i}",
                    competition_id=f"league:{i % 20}",
                    sport="basketball",
                    title=f"Synthetic {i}",
                    provider="fixture",
                    demo=True,
                    starts_at=(instant + timedelta(days=i % 100 - 10)).isoformat(),
                    participants=[
                        {"id": key if i in (5, 20) else key + "suffix", "name": key, "short_name": "X"}
                    ],
                )
            )
        db.add_all(
            [
                Projection(id="history", feed_id=feed.id, event_id="matrix-1", data={}, removed=False),
                Projection(id="future", feed_id=feed.id, event_id="matrix-21", data={}, removed=False),
                Projection(id="excluded", feed_id=feed.id, event_id="matrix-2", data={}, removed=False),
            ]
        )
        config = {
            **user.config,
            "follows": [{"type": "team", "source_key": key}],
            "event_overrides": [
                {"event_key": "matrix:game:2", "state": "exclude"},
                {"event_key": "matrix:game:31", "state": "include"},
            ],
        }
        db.commit()
    with sessions() as db:
        existing = {p.event_id: p for p in db.scalars(select(Projection))}
        # Independent full-scan oracle preserving the previous publication semantics.
        all_events = db.scalars(select(Event)).all()
        overrides = {x["event_key"]: x["state"] for x in config["event_overrides"]}
        expected = {
            e.id
            for e in all_events
            if included(e, config)
            or (
                e.id in existing
                and not existing[e.id].removed
                and event_is_past(e, instant)
                and overrides.get(e.source_key) != "exclude"
            )
        }
    loaded = []

    def observe(event, _):
        loaded.append(event.id)

    sql_event.listen(Event, "load", observe)
    try:
        with sessions() as db:
            existing = {p.event_id: p for p in db.scalars(select(Projection))}
            selected, _, _ = select_feed_events(db, config, existing, instant)
            assert {e.id for e in selected} == expected == {"matrix-1", "matrix-5", "matrix-20", "matrix-31"}
            assert len(loaded) == 6  # Only exact membership, explicit includes and prior projections.
            # Competition and event follows remain supported by the same boundary.
            for follows in [[], [{"source_key": "league:1"}], [{"source_key": "matrix:game:9"}]]:
                check = {**config, "follows": follows, "event_overrides": []}
                selected, _, _ = select_feed_events(db, check, {}, instant)
                assert {e.id for e in selected} == {e.id for e in all_events if included(e, check)}
    finally:
        sql_event.remove(Event, "load", observe)


def test_parallel_pending_publications_coalesce_per_owner(disk_stack):
    sessions, _ = disk_stack
    with sessions() as db:
        ensure_user(db, "other")
        db.execute(delete(Job))
        db.commit()
    barrier = Barrier(3)

    def publish(_):
        barrier.wait(timeout=5)
        with sessions() as db:
            for _ in range(10):
                enqueue(db, "projection", {"user_id": "local-reviewer"})
            db.commit()

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(publish, range(3)))
    with sessions() as db:
        enqueue(db, "projection", {"user_id": "other"})
        db.commit()
        assert db.scalar(select(func.count()).select_from(Job)) == 2
        assert all(job.state == "pending" and job.attempts == 0 for job in db.scalars(select(Job)))


def test_running_and_retrying_publications_do_not_absorb_new_state(stack):
    import app.jobs as jobs
    from app.worker import run_one
    from tests.test_calendar_flow import insert_event

    _, sessions = stack
    eid = insert_event(sessions)
    with sessions() as db:
        enqueue(db, "projection", {"user_id": "local-reviewer"})
        db.commit()
        claim, _ = jobs.claim_job(db)
        db.commit()
        assert claim
        user = db.get(User, "local-reviewer")
        for zone in ["UTC", "Europe/London"]:
            save_config(
                db,
                user,
                {
                    **user.config,
                    "event_overrides": [{"event_key": db.get(Event, eid).source_key, "state": "include"}],
                    "preferences": {
                        **user.config["preferences"],
                        "timezone": zone,
                        "transparent": zone == "UTC",
                    },
                },
                user.revision,
            )
        db.commit()
        pending = db.scalars(select(Job).where(Job.state == "pending")).all()
        assert len(pending) == 1
        successor = pending[0].id
        assert db.get(Job, claim.id).state == "running"
        jobs.fail_job(db, claim, ValueError("SYNTHETIC_RETRY"))
        db.commit()
    assert run_one(successor)
    with sessions() as db:
        assert db.get(User, "local-reviewer").config["preferences"]["timezone"] == "Europe/London"
        assert db.get(Job, successor).state == "done"
        feed = db.scalar(select(Feed).where(Feed.owner_id == "local-reviewer"))
        projection = db.scalar(
            select(Projection).where(Projection.feed_id == feed.id, Projection.event_id == eid)
        )
        assert projection.data["transparent"] is False and "TRANSP:OPAQUE" in feed.body
        # A retry retains its delay and is not mistaken for fresh pending work.
        retry = db.get(Job, claim.id)
        deadline = retry.due_at
        assert retry.state == "pending" and retry.attempts == 1
        assert enqueue(db, "projection", {"user_id": "local-reviewer"})
        db.commit()
        assert retry.due_at == deadline


def test_video_rematch_retires_association_outside_new_date_candidates(stack, monkeypatch):
    import app.content as content
    from tests.test_content import setup_content, api_video, CHANNEL, VID

    _, sessions = stack
    eid = setup_content(sessions)
    with sessions() as db:
        video = db.get(Video, VID)
        assert db.scalar(select(Link).where(Link.event_id == eid)).available
        event = db.get(Event, eid)
        event.starts_at = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
        db.commit()
        monkeypatch.setattr(content, "youtube_request", lambda *_: {"items": [api_video(video.title)]})
        content.refresh_videos(db, CHANNEL, [VID])
        db.commit()
        assert db.scalar(select(VideoMatch).where(VideoMatch.event_id == eid)).decision == "retired"
        assert not db.scalar(select(Link).where(Link.event_id == eid)).available
