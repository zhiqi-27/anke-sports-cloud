import os

from cryptography.fernet import Fernet

# Isolated database and identity; never use the running preview or cloud account.
os.environ["ANKE_SPORTS_ENV"] = "local"
os.environ["ANKE_SPORTS_LOCAL_PREVIEW"] = "true"
os.environ["ANKE_SPORTS_DATABASE_URL"] = "sqlite://"
os.environ["ANKE_SPORTS_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event as sql_event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
import app.worker as worker
import app.oauth as oauth
import app.db as database


@pytest.fixture
def mysql_engine():
    from tests.mysql_support import disposable_mysql

    with disposable_mysql() as engine:
        yield engine


@pytest.fixture
def stack(monkeypatch, mysql_engine):
    engine = mysql_engine or create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def db_override():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(oauth, "SessionLocal", sessions)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    client = TestClient(app, headers={"Origin": "http://127.0.0.1:3000"})
    client.post("/api/v1/auth/local").raise_for_status()
    while worker.run_one():
        pass
    yield client, sessions
    client.close()
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def disk_stack(tmp_path, monkeypatch, mysql_engine):
    from app.service import ensure_user
    from tests.test_calendar_flow import insert_event

    # Independent connections on a real file; StaticPool is not concurrency evidence.
    engine = mysql_engine or create_engine(
        "sqlite:///" + str(tmp_path / "concurrent.db"), connect_args={"check_same_thread": False}
    )

    if mysql_engine is None:

        @sql_event.listens_for(engine, "connect")
        def pragmas(connection, _):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=3000")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    with sessions() as db:
        ensure_user(db, "local-reviewer")
        db.commit()
    ident = insert_event(sessions)
    yield sessions, ident
    engine.dispose()
