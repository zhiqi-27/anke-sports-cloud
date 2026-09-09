"""Real loopback notification -> worker -> 1000 feeds; synthetic upstream only.

Fixtures start with 30 published events per feed, 20k events and 200 creators.
The initial snapshots are fixture construction, not part of measured latency.
No access to the main database; no credentials/private Feed paths in reports.
"""

# ruff: noqa: E402
import argparse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import socket
import tempfile
from threading import Thread
import time

from cryptography.fernet import Fernet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deadline", type=int, default=150)
    parser.add_argument(
        "--burst", action="store_true", help="Also send three distinct hints for unchanged metadata"
    )
    args = parser.parse_args()
    if args.output.exists() or not 30 <= args.deadline <= 600:
        parser.error("Use a new output and a deadline between 30 and 600 seconds")
    if os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("ANKE_SPORTS_ENV", "local") != "local":
        parser.error("Local isolated experiment only")
    with tempfile.TemporaryDirectory(prefix="anke-content-capacity-") as directory:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            os.environ.update(
                {
                    "ANKE_SPORTS_ENV": "local",
                    "ANKE_SPORTS_LOCAL_PREVIEW": "true",
                    "ANKE_SPORTS_DATABASE_URL": f"sqlite:///{directory}/capacity.db",
                    "ANKE_SPORTS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
                    "ANKE_SPORTS_FIREBASE_PROJECT_ID": "",
                    "ANKE_SPORTS_PUBLIC_URL": f"http://127.0.0.1:{port}",
                    "ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED": "false",
                    "ANKE_SPORTS_BROADCAST_CHECKS_ENABLED": "false",
                    "YOUTUBE_API_KEY": "",
                }
            )
            run(args, listener)


def run(args, listener):
    import httpx
    import uvicorn
    from icalendar import Calendar
    from sqlalchemy import event as sql_event, insert, select, func
    import app.content as content
    from app.calendar import projection_data, serialize
    from app.config import settings
    from app.db import (
        Base,
        ChannelSync,
        Creator,
        Event,
        Feed,
        Job,
        Link,
        Projection,
        User,
        engine,
        SessionLocal,
    )
    from app.main import app, mcp_lifespan
    from app.schemas import Config, CreatorFollow
    from app.security import digest
    from app.worker import run_one

    instant = datetime.now(timezone.utc).replace(microsecond=0)
    channel, vid = "UC" + "a" * 22, "abcdefghijk"
    video_url = f"https://www.youtube.com/watch?v={vid}"
    Base.metadata.create_all(engine)
    events = []
    for i in range(20000):
        target = i < 30
        start = instant + timedelta(days=(2 + i * 3) if target else i % 170, minutes=i % 120)
        events.append(
            {
                "id": f"fanout-event-{i}",
                "source_key": f"fixture:game:{i}",
                "competition_id": "fixture:target" if target else f"fixture:league:{i % 200}",
                "title": "Lakers vs Warriors" if target else f"Synthetic game {i}",
                "starts_at": start.isoformat(),
                "local_date": start.date().isoformat(),
                "sport": "basketball",
                "duration": 120,
                "provider": "fixture",
                "demo": True,
                "participants": [
                    {
                        "id": "fixture:LAL" if target else f"fixture:team:{i % 200}",
                        "name": "Lakers" if target else f"Synthetic {i}",
                        "short_name": "LAL" if target else f"T{i}",
                    },
                    {
                        "id": "fixture:GSW" if target else f"fixture:away:{i % 200}",
                        "name": "Warriors" if target else f"Away {i}",
                        "short_name": "GSW" if target else f"A{i}",
                    },
                ],
            }
        )
    tokens, users, feeds, projections, original = [], [], [], [], {}
    with SessionLocal() as db:
        db.execute(insert(Event), events)
        db.execute(
            insert(Creator),
            [
                {
                    "channel_id": channel if i == 0 else f"UC{i:022d}",
                    "name": f"Synthetic creator {i}",
                    "uploads_id": f"UUfixture{i}",
                }
                for i in range(200)
            ],
        )
        targets = db.scalars(select(Event).where(Event.competition_id == "fixture:target")).all()
        base_config = Config().model_dump()
        base_config["follows"] = [{"type": "team", "source_key": "fixture:LAL"}]
        base_config["creators"] = [CreatorFollow(channel_id=channel).model_dump()]
        data = {e.id: projection_data(db, e, None, base_config, link_rows=[], broadcasts={}) for e in targets}
        for i in range(1000):
            owner, feed_id = f"fanout-user-{i}", f"fanout-feed-{i}"
            config = json.loads(json.dumps(base_config))
            config["creators"].append(CreatorFollow(channel_id=f"UC{1 + i % 199:022d}").model_dump())
            # Ten percent removed this video before it was discovered.
            if i % 10 == 0:
                config["link_overrides"] = [
                    {"event_key": "fixture:game:0", "url": video_url, "state": "block"}
                ]
            users.append({"id": owner, "config": config, "display_name": "Synthetic capacity owner"})
            rows = []
            for e in targets:
                row = {
                    "id": f"p{i}-{e.id}",
                    "feed_id": feed_id,
                    "event_id": e.id,
                    "version": 1,
                    "updated_at": instant.isoformat(),
                    "removed": False,
                    "data": data[e.id],
                    "content_hash": digest(json.dumps(data[e.id], sort_keys=True, ensure_ascii=False)),
                }
                projections.append(row)
                rows.append(Projection(**row))
            body = serialize(rows).decode()
            token = secrets.token_urlsafe(32)
            tokens.append(token)
            original[feed_id] = (digest(body), instant.isoformat())
            feeds.append(
                {
                    "id": feed_id,
                    "owner_id": owner,
                    "token_hash": digest(token),
                    "token_ciphertext": settings().cipher().encrypt(token.encode()).decode(),
                    "body": body,
                    "etag": digest(body),
                    "revision": 1,
                    "updated_at": instant.isoformat(),
                }
            )
        db.execute(insert(User), users)
        db.execute(insert(Feed), feeds)
        db.execute(insert(Projection), projections)
        secret = secrets.token_bytes(32)
        sync = ChannelSync(
            channel_id=channel,
            state="verified",
            secret_ciphertext=settings().cipher().encrypt(secret).decode(),
            lease_expires_at=(instant + timedelta(days=1)).isoformat(),
        )
        db.add(sync)
        db.flush()
        callback = sync.callback_id
        db.commit()
    print("Seeded 20,000 events, 1,000 published 30-event feeds, 200 active creators", flush=True)

    requests = 0

    def synthetic_video(endpoint, params):
        nonlocal requests
        assert endpoint == "videos" and params["id"] == vid
        requests += 1
        return {
            "items": [
                {
                    "id": vid,
                    "snippet": {
                        "channelId": channel,
                        "title": f"Lakers Warriors {events[0]['local_date']} preview",
                        "description": "Synthetic fixture",
                        "publishedAt": instant.isoformat(),
                    },
                    "status": {"privacyStatus": "public"},
                }
            ]
        }

    content.youtube_request = synthetic_video

    @asynccontextmanager
    async def lifecycle(application):
        async with mcp_lifespan():
            yield

    app.router.lifespan_context = lifecycle
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    wait = time.monotonic() + 10
    while not server.started and time.monotonic() < wait:
        time.sleep(0.02)
    assert server.started
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    queries, hydrated = 0, 0

    def count_sql(*args):
        nonlocal queries
        queries += 1

    def count_event(*args):
        nonlocal hydrated
        hydrated += 1

    sql_event.listen(engine, "before_cursor_execute", count_sql)
    sql_event.listen(Event, "load", count_event)
    report = {
        "environment": "temporary SQLite; actual loopback HTTP and single worker; synthetic YouTube adapter",
        "source_sha256": {
            p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
            for p in (
                "app/content.py",
                "app/calendar.py",
                "app/service.py",
                "app/jobs.py",
                "experiments/content_capacity.py",
            )
        },
        "recorded_at": instant.isoformat(),
        "accounts": 1000,
        "events": 20000,
        "creators": 200,
        "events_per_feed": 30,
        "eligible_owners": 900,
        "blocked_owners": 100,
        "deadline_seconds": args.deadline,
    }
    body = f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>{vid}</yt:videoId><yt:channelId>{channel}</yt:channelId></entry></feed>'.encode()
    headers = {"X-Hub-Signature": "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()}
    try:
        with httpx.Client(base_url=base, timeout=30, trust_env=False) as http:
            begin = time.perf_counter()
            assert (
                http.post("/webhooks/youtube/" + callback, content=body, headers=headers).status_code == 204
            )
            report["notification_ack_ms"] = round((time.perf_counter() - begin) * 1000, 2)
            # An exact replay is ignored by the receipt boundary.
            assert (
                http.post("/webhooks/youtube/" + callback, content=body, headers=headers).status_code == 204
            )
            completions, processed = {}, 0
            while time.perf_counter() - begin < args.deadline and run_one():
                processed += 1
                with SessionLocal() as db:
                    for fid, revision in db.execute(select(Feed.id, Feed.revision).where(Feed.revision > 1)):
                        completions.setdefault(fid, time.perf_counter() - begin)
                if processed % 100 == 0:
                    print(
                        json.dumps(
                            {
                                "processed": processed,
                                "changed_feeds": len(completions),
                                "elapsed_s": round(time.perf_counter() - begin, 2),
                            }
                        ),
                        flush=True,
                    )
            drained_at = time.perf_counter()
            values = sorted(completions.values())
            with SessionLocal() as db:
                pending = db.scalar(
                    select(func.count()).select_from(Job).where(Job.state.in_(["pending", "running"]))
                )
                failed = db.scalar(select(func.count()).select_from(Job).where(Job.state == "failed"))
                actual = {f.id: f for f in db.scalars(select(Feed))}
                # Every retained event identity must survive. Exactly the new link changes content.
                assert set(
                    db.execute(select(Projection.id, Projection.feed_id, Projection.event_id)).all()
                ) == {(p["id"], p["feed_id"], p["event_id"]) for p in projections}
                for i in range(1000):
                    entries = Calendar.from_ical(actual[f"fanout-feed-{i}"].body).walk("VEVENT")
                    assert {str(e["UID"]) for e in entries} == {
                        f"p{i}-fanout-event-{j}@calendar.anke-sports" for j in range(30)
                    }
                assert all(
                    (actual[f"fanout-feed-{i}"].etag, actual[f"fanout-feed-{i}"].updated_at)
                    == original[f"fanout-feed-{i}"]
                    for i in range(0, 1000, 10)
                )
                assert not db.scalar(
                    select(func.count())
                    .select_from(Link)
                    .where(Link.owner_id.in_([f"fanout-user-{i}" for i in range(0, 1000, 10)]))
                )
                report.update(
                    {
                        "processed_jobs": processed,
                        "changed_feeds": len(completions),
                        "pending_jobs": pending,
                        "failed_jobs": failed,
                        "upstream_adapter_requests": requests,
                        "sql_statements": queries,
                        "event_objects_loaded": hydrated,
                        "drain_seconds": round(drained_at - begin, 2),
                        "stable_uids": True,
                        "uids_checked_in_published_ics": 30000,
                        "blocked_feeds_unchanged": True,
                        "p95_seconds": round(values[math.ceil(len(values) * 0.95) - 1], 2)
                        if values
                        else None,
                        "all_eligible_published": len(completions) == 900,
                        "target_met": len(completions) == 900
                        and pending == 0
                        and values[math.ceil(len(values) * 0.95) - 1] < 120,
                    }
                )
            sample = []
            for i in [0, 1, 999]:
                response = http.get(f"/feeds/{tokens[i]}.ics")
                assert response.status_code == 200
                assert (video_url in response.text) == (f"fanout-feed-{i}" in completions)
                assert f"p{i}-fanout-event-0@calendar.anke-sports" in response.text
                assert (
                    http.get(
                        f"/feeds/{tokens[i]}.ics", headers={"If-None-Match": response.headers["etag"]}
                    ).status_code
                    == 304
                )
                sample.append({"blocked": i % 10 == 0, "http_200_and_304": True})
            report["http_samples"] = sample
            if args.burst and report["all_eligible_published"] and not pending:
                with SessionLocal() as db:
                    before_burst = {
                        f.id: (f.revision, f.etag, f.updated_at) for f in db.scalars(select(Feed))
                    }
                burst_begin = time.perf_counter()
                before_requests = requests
                for index in range(3):
                    hint = body.replace(b"<entry>", f"<entry><title>Synthetic hint {index}</title>".encode())
                    signature = "sha256=" + hmac.new(secret, hint, hashlib.sha256).hexdigest()
                    assert (
                        http.post(
                            "/webhooks/youtube/" + callback,
                            content=hint,
                            headers={"X-Hub-Signature": signature},
                        ).status_code
                        == 204
                    )
                with SessionLocal() as db:
                    incoming = db.scalars(
                        select(Job.id).where(Job.kind == "youtube_videos", Job.state == "pending")
                    ).all()
                    assert len(incoming) == 3
                for job_id in incoming:
                    assert run_one(job_id)
                with SessionLocal() as db:
                    publications = db.scalar(
                        select(func.count())
                        .select_from(Job)
                        .where(Job.kind == "projection", Job.state == "pending")
                    )
                    assert publications == 1000
                drained = 0
                while time.perf_counter() - burst_begin < args.deadline and run_one():
                    drained += 1
                with SessionLocal() as db:
                    after_burst = {f.id: (f.revision, f.etag, f.updated_at) for f in db.scalars(select(Feed))}
                    remaining = db.scalar(
                        select(func.count()).select_from(Job).where(Job.state.in_(["pending", "running"]))
                    )
                    assert after_burst == before_burst
                report["unchanged_metadata_burst"] = {
                    "distinct_notifications": 3,
                    "upstream_adapter_requests": requests - before_requests,
                    "pending_publications_after_three_video_jobs": publications,
                    "processed_publications": drained,
                    "remaining": remaining,
                    "feed_versions_etags_timestamps_unchanged": True,
                    "duration_seconds": round(time.perf_counter() - burst_begin, 2),
                }
            with args.output.open("x") as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
                output.write("\n")
            print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        engine.dispose()
        assert not thread.is_alive()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Content capacity experiment failed; private URLs and credentials withheld"
        ) from None
