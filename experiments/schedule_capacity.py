"""Disposable 20k active-event / 1000-account / 200-creator HTTP capacity experiment.

No upstream calls, main database access or browser automation. Credentials and
private Feed paths remain in memory; only timings, counts and assertions escape.
"""

# ruff: noqa: E402
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import hashlib
import math
import os
from pathlib import Path
import secrets
import socket
import tempfile
from threading import Lock, Thread
import time

from cryptography.fernet import Fernet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.samples <= 100 or not 1 <= args.concurrency <= 8 or args.output.exists():
        parser.error("Use 1–100 samples, 1–8 concurrency, and a new output file")
    if os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("ANKE_SPORTS_ENV", "local") != "local":
        parser.error("This experiment runs only in a local process")
    with tempfile.TemporaryDirectory(prefix="anke-schedule-capacity-") as directory:
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
                }
            )
            run(args, listener)


def run(args, listener):
    import httpx
    import uvicorn
    from sqlalchemy import event as sql_event, insert, select
    from app.calendar import rebuild_feed
    from app.config import settings
    from app.db import Base, Creator, Event, Feed, Link, Session, Source, User, engine, SessionLocal
    from app.main import app, mcp_lifespan
    from app.schemas import Config
    from app.security import digest

    instant = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    Base.metadata.create_all(engine)
    keys = [f"capacity:team:{i}" for i in range(200)]
    sources = [
        {
            "id": key,
            "name": f"合成球队 {i}",
            "short_name": str(i),
            "sport": "basketball",
            "kind": "team",
            "color": "#777777",
            "provider": "capacity",
            "demo": True,
        }
        for i, key in enumerate(keys)
    ]
    creators = [{"channel_id": f"UC{i:022d}", "name": f"合成创作者 {i}"} for i in range(200)]
    events = []
    for i in range(40000):
        start = instant + timedelta(minutes=(i % 20000) * 2, days=0 if i < 20000 else -365)
        events.append(
            {
                "id": f"capacity-event-{i}",
                "source_key": f"capacity:event:{i}",
                "competition_id": f"capacity:league:{i % 20}",
                "sport": "basketball",
                "title": f"合成比赛 {i}",
                "starts_at": start.isoformat(),
                "local_date": start.date().isoformat(),
                "participants": [
                    {"id": keys[i % 200], "name": "合成球队", "short_name": "TEST", "color": "#777777"},
                    {"id": keys[(i + 1) % 200], "name": "合成球队", "short_name": "TEST", "color": "#777777"},
                ],
                "provider": "capacity",
                "demo": True,
            }
        )
    accounts, sessions, feeds, links, tokens = [], [], [], [], []
    feed_token = None
    for i in range(1000):
        owner = f"capacity-user-{i}"
        config = Config().model_dump()
        config["follows"] = [{"type": "team", "source_key": keys[i % 200]}]
        config["creators"] = [
            {
                "channel_id": creators[i % 200]["channel_id"],
                "scope_keys": [],
                "preview": True,
                "recap": True,
                "enabled": True,
            }
        ]
        accounts.append({"id": owner, "display_name": "合成容量账号", "config": config})
        token = secrets.token_urlsafe(32)
        tokens.append(token)
        sessions.append(
            {
                "token_hash": digest(token),
                "user_id": owner,
                "expires_at": (instant + timedelta(days=2)).isoformat(),
            }
        )
        raw = secrets.token_urlsafe(32)
        if i == 0:
            feed_token = raw
        feeds.append(
            {
                "id": f"capacity-feed-{i}",
                "owner_id": owner,
                "token_hash": digest(raw),
                "token_ciphertext": settings().cipher().encrypt(raw.encode()).decode(),
            }
        )
        for j in range(10):
            url = f"https://www.youtube.com/watch?v={i * 10 + j:011d}"
            links.append(
                {
                    "owner_id": owner,
                    "event_id": f"capacity-event-{(i + j * 200) % 20000}",
                    "url": url,
                    "url_hash": digest(url),
                    "title": f"合成私人 · {owner}",
                    "kind": "preview",
                    "platform": "YouTube",
                    "origin": "manual",
                }
            )
    with engine.begin() as connection:
        for model, rows in (
            (Source, sources),
            (Creator, creators),
            (Event, events),
            (User, accounts),
            (Session, sessions),
            (Feed, feeds),
            (Link, links),
        ):
            for start in range(0, len(rows), 1000):
                connection.execute(insert(model), rows[start : start + 1000])
    queries = 0
    query_lock = Lock()

    @sql_event.listens_for(engine, "before_cursor_execute")
    def count_queries(*_):
        nonlocal queries
        with query_lock:
            queries += 1

    before = time.perf_counter()
    with SessionLocal() as db:
        rebuild_feed(db, "capacity-user-0")
        db.commit()
        feed = db.scalar(select(Feed).where(Feed.owner_id == "capacity-user-0"))
        etag = feed.etag
        body_size = len(feed.body.encode())
        assert feed.body.count("BEGIN:VEVENT") == 200
    publication = {
        "seconds": round(time.perf_counter() - before, 4),
        "sql_statements": queries,
        "events": 200,
        "body_bytes": body_size,
    }

    @asynccontextmanager
    async def lifecycle(_):
        # Fixture seeding is above; do not add the ordinary 48-event demo.
        async with mcp_lifespan():
            yield

    app.router.lifespan_context = lifecycle
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="critical"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "Isolated HTTP server did not start"
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    params = {
        "from": instant.isoformat(),
        "to": (instant + timedelta(days=30)).isoformat(),
        "dataset": "demo",
        "limit": 100,
    }
    report = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": {
            path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in ("app/actions.py", "app/calendar.py", "app/public_feeds.py")
        },
        "environment": "local SQLite, real loopback HTTP; synthetic data only",
        "active_events": 20000,
        "historical_events": 20000,
        "stored_accounts": 1000,
        "distinct_creators": 200,
        "private_links": 10000,
        "published_feeds": 1,
        "concurrency": args.concurrency,
        "samples_per_case": args.samples,
        "publication": publication,
        "cases": {},
    }
    try:
        with httpx.Client(base_url=base, timeout=120) as http:

            def request(case, i):
                begin = time.perf_counter()
                if case.startswith("feed"):
                    headers = {"If-None-Match": f'"{etag}"'} if case == "feed_304" else {}
                    response = http.get(f"/feeds/{feed_token}.ics", headers=headers)
                    assert response.status_code == (304 if headers else 200), "Feed response mismatch"
                    if headers:
                        assert not response.content
                    else:
                        assert len(response.content) == body_size
                else:
                    headers = {} if case == "anonymous" else {"Cookie": f"anke_sports_session={tokens[i]}"}
                    query = {**params, "followed": case == "followed"}
                    response = http.get("/api/v1/events", params=query, headers=headers)
                    assert response.status_code == 200, "Schedule response failed"
                    rows = response.json()["items"]
                    assert len(rows) == 100 and response.json()["next_cursor"], "Pagination mismatch"
                    assert all(row["demo"] for row in rows)
                    for row in rows:
                        assert all(link["title"] == f"合成私人 · capacity-user-{i}" for link in row["links"])
                        if case == "anonymous":
                            assert not row["links"]
                        if case == "followed":
                            assert row["included"]
                return time.perf_counter() - begin

            for case in ("anonymous", "personal", "followed", "feed_200", "feed_304"):
                request(case, 0)  # Warm DB/HTTP path, no application result cache.
                start_queries = queries
                with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                    values = list(pool.map(lambda i: request(case, i), range(args.samples)))
                values.sort()
                report["cases"][case] = {
                    "p50_ms": round(values[len(values) // 2] * 1000, 2),
                    "p95_ms": round(values[math.ceil(len(values) * 0.95) - 1] * 1000, 2),
                    "max_ms": round(max(values) * 1000, 2),
                    "sql_statements_total": queries - start_queries,
                    "samples": len(values),
                }
                print(json.dumps({case: report["cases"][case]}), flush=True)
        report["limits"] = [
            "Stored account counts do not mean 1000 concurrent users",
            "One 200-event Feed was published, not all 1000 feeds",
            "No notification-to-Feed, MySQL, Azure, device or upstream API test",
        ]
        with args.output.open("x") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        engine.dispose()
        assert not thread.is_alive(), "Isolated server did not stop"


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Capacity experiment failed; request details and credentials withheld") from None
