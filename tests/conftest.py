import os

from cryptography.fernet import Fernet

# Isolated database and identity; never use the running preview or cloud account.
os.environ["ANKE_SPORTS_ENV"] = "local"
os.environ["ANKE_SPORTS_LOCAL_PREVIEW"] = "true"
os.environ["ANKE_SPORTS_DATABASE_URL"] = "sqlite://"
os.environ["ANKE_SPORTS_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
import app.worker as worker


@pytest.fixture
def stack(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def db_override():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    client = TestClient(app, headers={"Origin": "http://127.0.0.1:3000"})
    client.post("/api/v1/auth/local").raise_for_status()
    while worker.run_one():
        pass
    yield client, sessions
    client.close()
    app.dependency_overrides.clear()
    engine.dispose()
