import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_broadcast_migration_preserves_publications_and_roundtrips(tmp_path):
    target = tmp_path / "migration.db"
    env = {**os.environ, "ANKE_SPORTS_DATABASE_URL": f"sqlite:///{target}"}

    def alembic(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args], env=env, capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    alembic("upgrade", "a8c502e7d134")
    with sqlite3.connect(target) as db:
        publication = json.dumps(
            {"url": "https://www.nba.com/game/migration-fixture", "valid_until": "2030-01-01T05:00:00+05:00"}
        )
        db.execute(
            "INSERT INTO broadcast_records (link_id,revision,status,draft,published,published_revision,device_tests,network_status,next_check_at,missing_count,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "fixture",
                4,
                "published",
                "{}",
                publication,
                4,
                "[]",
                "reachable",
                "2030-01-01T06:00:00+00:00",
                0,
                "2026-01-01T00:00:00+00:00",
            ),
        )
        before = db.execute("SELECT * FROM broadcast_records").fetchone()
        columns = [r[1] for r in db.execute("PRAGMA table_info(broadcast_records)")]
    alembic("upgrade", "head")
    alembic("check")
    with sqlite3.connect(target) as db:
        after = dict(
            zip(
                [r[1] for r in db.execute("PRAGMA table_info(broadcast_records)")],
                db.execute("SELECT * FROM broadcast_records").fetchone(),
            )
        )
        assert after["expires_at"] == "" and after["next_check_at"] == ""
        assert {key: after[key] for key in columns if key != "next_check_at"} == {
            key: value for key, value in zip(columns, before) if key != "next_check_at"
        }
    alembic("downgrade", "a8c502e7d134")
    with sqlite3.connect(target) as db:
        assert "expires_at" not in [r[1] for r in db.execute("PRAGMA table_info(broadcast_records)")]
        assert db.execute("SELECT published FROM broadcast_records").fetchone()[0] == publication
    alembic("upgrade", "head")
    alembic("check")
    # Offline dialect compilation only; no connection to a MySQL server.
    env["ANKE_SPORTS_DATABASE_URL"] = "mysql+pymysql://fixture:fixture@localhost/anke_sports"
    ddl = alembic("upgrade", "a8c502e7d134:head", "--sql")
    assert (
        "ADD COLUMN expires_at" in ddl
        and "CREATE INDEX ix_broadcast_due" in ddl
        and "CREATE INDEX ix_broadcast_expiry" in ddl
    )
    assert Path(target).is_file()
