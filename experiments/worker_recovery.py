"""Kill a real local worker and restore its independent SQLite backup. No cloud or main DB."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if os.getenv("WEBSITE_INSTANCE_ID") or os.getenv("ANKE_SPORTS_ENV", "local") != "local":
        raise RuntimeError("LOCAL_EXPERIMENT_ONLY")
    with tempfile.TemporaryDirectory(prefix="anke-worker-recovery-") as folder:
        from cryptography.fernet import Fernet

        root = Path(folder)
        os.environ.update(
            {
                "ANKE_SPORTS_ENV": "local",
                "ANKE_SPORTS_DATABASE_URL": "sqlite:///" + str(root / "source.db"),
                "ANKE_SPORTS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
                "ANKE_SPORTS_LOCAL_PREVIEW": "true",
                "ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED": "false",
                "ANKE_SPORTS_BROADCAST_CHECKS_ENABLED": "false",
            }
        )
        from datetime import datetime, timedelta, timezone
        from icalendar import Calendar
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker
        from app.db import Base, Event, Feed, Job, JobReplay, SessionLocal, engine, now
        from app.config import settings
        from app.service import ensure_user, save_config
        from app.worker import run_one

        Base.metadata.create_all(engine)
        owner_id = "recovery-fixture"
        with SessionLocal() as db:
            owner = ensure_user(db, owner_id)
            event = Event(
                source_key="fixture:recovery",
                competition_id="fixture:league",
                sport="basketball",
                title="【合成恢复演练】原赛程",
                starts_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                local_date=(datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(),
                participants=[],
                provider="FIXTURE",
                demo=True,
            )
            db.add(event)
            save_config(
                db,
                owner,
                {**owner.config, "event_overrides": [{"event_key": event.source_key, "state": "include"}]},
                owner.revision,
            )
            db.commit()
        while run_one():
            pass
        with SessionLocal() as db:
            initial = db.scalar(select(Feed).where(Feed.owner_id == owner_id)).body
            event = db.scalar(select(Event))
            event.title = "【合成恢复演练】已改期"
            event.starts_at = (datetime.fromisoformat(event.starts_at) + timedelta(hours=1)).isoformat()
            pending = Job(kind="projection", payload={"user_id": owner_id})
            db.add(pending)
            db.commit()
            job_id = pending.id

        marker = root / "staged"
        child_code = """
import sys,time
from pathlib import Path
from app import worker
def stage(db, claim):
    worker.rebuild_feed(db, claim.payload["user_id"])
    db.flush()
    Path(sys.argv[2]).write_text("staged")
    while True:
        time.sleep(1)
worker.execute_claim = stage
worker.run_one(sys.argv[1])
"""
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, job_id, str(marker)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 15
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert marker.exists() and child.poll() is None, "Child did not reach its uncommitted stage"
            child.kill()
            child.wait(timeout=10)
            assert child.returncode < 0
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            child.stderr.close()

        with SessionLocal() as db:
            persisted = db.get(Job, job_id)
            assert persisted.state == "running" and persisted.attempts == 1
            assert db.scalar(select(Feed)).body == initial
            # The process is confirmed terminal. Advance ONLY this fixture's lease clock.
            persisted.due_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            db.commit()
        assert run_one(job_id)
        with SessionLocal() as db:
            recovered = db.scalar(select(Feed)).body
            before, after = (
                Calendar.from_ical(initial).walk("VEVENT")[0],
                Calendar.from_ical(recovered).walk("VEVENT")[0],
            )
            assert str(before["UID"]) == str(after["UID"])
            assert int(after["SEQUENCE"]) == int(before["SEQUENCE"]) + 1
            assert "已改期" in str(after["SUMMARY"])
            assert db.get(Job, job_id).state == "done" and db.get(Job, job_id).attempts == 2
        assert run_one(job_id) is False

        failed_id = None
        with SessionLocal() as db:
            failed = Job(
                kind="projection",
                payload={"user_id": owner_id},
                state="failed",
                attempts=5,
                error="WORKER_LEASE_EXPIRED",
            )
            db.add(failed)
            db.commit()
            failed_id = failed.id
        command = [
            sys.executable,
            "-m",
            "scripts.recover_jobs",
            "--environment",
            "local",
            "--job-id",
            failed_id,
            "--expected-attempts",
            "5",
            "--reason",
            "合成恢复演练，验证人工重放",
        ]

        def cli(extra):
            result = subprocess.run([*command, *extra], capture_output=True, text=True, timeout=15)
            assert result.returncode == 0, "Recovery CLI failed"
            return json.loads(result.stdout)

        preview = cli([])
        assert preview["dry_run"] and not preview["created"]
        replay = cli(["--apply"])
        duplicate = cli(["--apply"])
        assert (
            replay["created"] and not duplicate["created"] and replay["new_job_id"] == duplicate["new_job_id"]
        )
        assert run_one(replay["new_job_id"])
        with SessionLocal() as db:
            assert db.get(Job, failed_id).state == "failed" and db.get(JobReplay, failed_id)
            assert db.scalar(select(Feed)).body == recovered

        backup, restored = root / "backup.db", root / "restored.db"
        with sqlite3.connect(root / "source.db") as source, sqlite3.connect(backup) as target:
            source.backup(target)
        backup.chmod(0o600)
        with sqlite3.connect(backup) as source, sqlite3.connect(restored) as target:
            source.backup(target)
        restored.chmod(0o600)
        with sqlite3.connect(root / "source.db") as source, sqlite3.connect(restored) as target:
            assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            tables = [r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                assert sorted(source.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr) == sorted(
                    target.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr
                ), table
        restore_engine = create_engine("sqlite:///" + str(restored))
        with sessionmaker(restore_engine)() as db:
            restored_feed = db.scalar(select(Feed))
            assert restored_feed.body == recovered
            token = settings().cipher().decrypt(restored_feed.token_ciphertext.encode()).decode()
            assert hashlib.sha256(token.encode()).hexdigest() == restored_feed.token_hash
            del token
        restore_engine.dispose()
        engine.dispose()
        report = {
            "observed_at": now(),
            "environment": "temporary_local_sqlite",
            "synthetic": True,
            "real_child_killed_after_flush": True,
            "uncommitted_feed_rolled_back": True,
            "fixture_lease_clock_advanced_after_confirmed_exit": True,
            "recovered_attempt": 2,
            "uid_preserved": True,
            "sequence_before": int(before["SEQUENCE"]),
            "sequence_after": int(after["SEQUENCE"]),
            "duplicate_execution_noop": True,
            "cli_dry_run_no_write": True,
            "cli_one_audited_replay": True,
            "restored_tables_equal": len(tables),
            "feed_credential_restored_with_retained_encryption_key": True,
            "integrity_check": "ok",
            "feed_sha256_after_restore": hashlib.sha256(recovered.encode()).hexdigest(),
            "main_database_touched": False,
            "external_network_used": False,
            "mysql_or_azure_verified": False,
        }
        if args.output:
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
