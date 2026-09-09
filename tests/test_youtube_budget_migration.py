import os
import sqlite3
import subprocess
import sys


def test_budget_migration_preserves_job_attempts_and_roundtrips(tmp_path):
    target = tmp_path / "migration.db"
    env = {**os.environ, "ANKE_SPORTS_DATABASE_URL": f"sqlite:///{target}"}

    def alembic(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args], env=env, capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    alembic("upgrade", "c72b961e430a")
    with sqlite3.connect(target) as db:
        db.execute(
            "INSERT INTO outbox (id,kind,payload,state,attempts,due_at,error,created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("fixture", "youtube_videos", "{}", "pending", 3, "2030-01-01", "FIXTURE", "2026-01-01"),
        )
        columns = [r[1] for r in db.execute("PRAGMA table_info(outbox)")]
        before = db.execute("SELECT * FROM outbox").fetchone()
    for _ in range(2):
        alembic("upgrade", "head")
        alembic("check")
        with sqlite3.connect(target) as db:
            after = db.execute("SELECT " + ",".join(columns) + " FROM outbox").fetchone()
            assert after == before
            assert db.execute("SELECT quota_waits FROM outbox").fetchone() == (0,)
            assert db.execute("SELECT COUNT(*) FROM youtube_budgets").fetchone() == (0,)
        alembic("downgrade", "c72b961e430a")
    env["ANKE_SPORTS_DATABASE_URL"] = "mysql+pymysql://fixture:fixture@localhost/anke_sports"
    ddl = alembic("upgrade", "c72b961e430a:head", "--sql")
    assert "CREATE TABLE youtube_budgets" in ddl and "ADD COLUMN quota_waits" in ddl
