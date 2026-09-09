"""Bounded scheduling and process-restart experiment; disposable SQLite, synthetic inputs."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def seed_broadcasts(sessions, count=20000, due=7, expired=3):
    from sqlalchemy import insert
    from app.db import BroadcastRecord, Event, Link
    from app.security import digest

    instant = datetime.now(timezone.utc)
    future = (instant + timedelta(days=2)).isoformat()
    events, links, records = [], [], []
    for i in range(count):
        ident = f"scheduler-{i:06d}"
        url = f"https://www.youtube.com/watch?v={i:011d}"
        expiry = (instant - timedelta(minutes=1)).isoformat() if i < expired else future
        events.append(
            dict(
                id=ident,
                source_key=f"fixture:{ident}",
                competition_id="fixture:league",
                sport="basketball",
                title="合成调度比赛",
                starts_at=future,
                provider="fixture",
                demo=True,
            )
        )
        links.append(
            dict(
                id=ident,
                owner_id="public",
                event_id=ident,
                url=url,
                url_hash=digest(url),
                title="合成巡检入口",
                kind="live",
                platform="YouTube",
                origin="official",
                available=True,
            )
        )
        publication = dict(
            event_id=ident,
            url=url,
            title="合成巡检入口",
            content_type="official_match",
            access="unknown",
            region_mode="unknown",
            regions=[],
            evidence_url=url,
            evidence_note="合成隔离证据",
            reviewed_at=instant.isoformat(),
            valid_until=expiry,
        )
        records.append(
            dict(
                link_id=ident,
                status="published",
                draft=publication,
                published=publication,
                revision=1,
                published_revision=1,
                expires_at=expiry,
                next_check_at=instant.isoformat() if i < expired + due else future,
            )
        )
    with sessions() as db:
        db.execute(insert(Event), events)
        db.execute(insert(Link), links)
        db.execute(insert(BroadcastRecord), records)
        db.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if (
        args.output.exists()
        or os.getenv("WEBSITE_INSTANCE_ID")
        or os.getenv("ANKE_SPORTS_ENV", "local") != "local"
    ):
        parser.error("Use a new output file and local environment")
    with tempfile.TemporaryDirectory(prefix="anke-scheduler-") as directory:
        from cryptography.fernet import Fernet

        os.environ.update(
            ANKE_SPORTS_ENV="local",
            ANKE_SPORTS_LOCAL_PREVIEW="true",
            ANKE_SPORTS_DATABASE_URL=f"sqlite:///{directory}/experiment.db",
            ANKE_SPORTS_ENCRYPTION_KEY=Fernet.generate_key().decode(),
            ANKE_SPORTS_FIREBASE_PROJECT_ID="",
            ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED="false",
            ANKE_SPORTS_BROADCAST_CHECKS_ENABLED="false",
        )
        run(args)


def run(args):
    from sqlalchemy import event as sql_event, select
    from app import broadcasts
    from app.config import settings
    from app.db import Base, BroadcastRecord, Job, ProviderState, SessionLocal, engine

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add(
            ProviderState(
                id="jolpica",
                enabled=True,
                last_success=(datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(),
            )
        )
        db.commit()
    # Each invocation is a fresh OS process running the real worker entry point.
    # Only external fetching is replaced; the durable scheduler/claim/completion stay real.
    child = """
import json
from app import worker
from app.db import ProviderState, now
calls=[]
def fixture_fetch(db, ident):
    calls.append(ident)
    db.get(ProviderState, ident).last_success=now()
worker.sync_provider=fixture_fetch
status=worker.main(["--once"])
print(json.dumps({"exit":status,"synthetic_fetches":len(calls)}))
"""
    restarts = []
    for _ in range(2):
        result = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, "Isolated worker restart failed"
        restarts.append(json.loads(result.stdout))
    assert restarts == [{"exit": 0, "synthetic_fetches": 1}, {"exit": 0, "synthetic_fetches": 0}]
    settings().broadcast_checks_enabled = True
    seed_broadcasts(SessionLocal)
    statements, loaded, candidate_queries = [], [], []

    def count_sql(_, __, sql, params, *rest):
        statements.append(sql)
        if sql.startswith("SELECT broadcast_records.link_id ") and len(candidate_queries) < 2:
            candidate_queries.append((sql, params))

    sql_event.listen(engine, "before_cursor_execute", count_sql)
    sql_event.listen(BroadcastRecord, "load", count_load := lambda row, _: loaded.append(row.link_id))
    start = time.perf_counter()
    counts = broadcasts.schedule_broadcasts()
    elapsed = time.perf_counter() - start
    assert counts == {"examined": 10, "normalized": 0, "expired": 3, "queued": 7}
    assert len(set(loaded)) == 10
    active_sql = len(statements)
    idle = []
    for _ in range(20):
        start = time.perf_counter()
        assert broadcasts.schedule_broadcasts()["examined"] == 0
        idle.append((time.perf_counter() - start) * 1000)
    assert len(set(loaded)) == 10
    sql_event.remove(engine, "before_cursor_execute", count_sql)
    sql_event.remove(BroadcastRecord, "load", count_load)
    with SessionLocal() as db:
        jobs = db.scalars(select(Job).where(Job.kind == "broadcast_check")).all()
        assert len(jobs) == 7 and all(j.state == "pending" for j in jobs)
        plans = {}
        assert len(candidate_queries) == 2
        for name, (sql, params) in zip(("expiry_candidates", "network_candidates"), candidate_queries):
            plans[name] = [
                row[3] for row in db.connection().exec_driver_sql("EXPLAIN QUERY PLAN " + sql, params)
            ]
        assert "ix_broadcast_expiry" in str(plans["expiry_candidates"])
        assert "ix_broadcast_due" in str(plans["network_candidates"])
    report = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "environment": "isolated local SQLite; synthetic data only",
        "events": 20000,
        "broadcast_records": 20000,
        "active_sweep": {
            **counts,
            "milliseconds": round(elapsed * 1000, 2),
            "sql_statements": active_sql,
            "distinct_records_hydrated": len(set(loaded)),
        },
        "idle_sweeps": {
            "samples": 20,
            "p95_ms": round(sorted(idle)[18], 2),
            "sql_statements_per_sweep": (len(statements) - active_sql) // 20,
            "records_hydrated": 0,
        },
        "fresh_worker_processes": restarts,
        "query_plans": plans,
        "source_sha256": {
            name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
            for name in ("app/broadcasts.py", "app/worker.py", "app/providers.py", "app/db.py")
        },
        "limits": [
            "No upstream or HEAD request; seven checks queued but not fetched",
            "No MySQL, Azure timer/queue, device, large user fanout or full backlog latency evidence",
        ],
    }
    with args.output.open("x") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(
        json.dumps(
            {key: report[key] for key in ("active_sweep", "idle_sweeps", "fresh_worker_processes")},
            ensure_ascii=False,
        )
    )
    engine.dispose()


if __name__ == "__main__":
    main()
