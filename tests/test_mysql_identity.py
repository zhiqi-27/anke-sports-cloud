"""Identifiers are exact values even under MySQL's default case/accent-insensitive database."""

import pytest
from sqlalchemy import select

from app.db import User, Feed
from app.service import ensure_user, save_config


@pytest.mark.parametrize(
    "first,second", [("CaseSensitiveUID", "casesensitiveuid"), ("viewer", "víewer"), ("viewer", "viewer ")]
)
def test_distinct_auth_subjects_never_share_a_calendar(stack, first, second):
    _, sessions = stack
    with sessions() as db:
        a = ensure_user(db, first)
        db.commit()
        b = ensure_user(db, second)
        assert b.id == second and a.id != b.id
        save_config(
            db, b, {**b.config, "preferences": {**b.config["preferences"], "timezone": "UTC"}}, b.revision
        )
        db.commit()
    with sessions() as db:
        assert db.get(User, first).config["preferences"]["timezone"] != "UTC"
        assert db.get(User, second).config["preferences"]["timezone"] == "UTC"
        first_feed = db.scalar(select(Feed).where(Feed.owner_id == first))
        second_feed = db.scalar(select(Feed).where(Feed.owner_id == second))
        assert first_feed.id != second_feed.id and first_feed.token_hash != second_feed.token_hash


def test_mysql_application_connections_use_read_committed(mysql_engine):
    if mysql_engine is None:
        pytest.skip("Requires explicitly disposable MySQL")
    with mysql_engine.connect() as db:
        assert db.exec_driver_sql("SELECT @@transaction_isolation").scalar() == "READ-COMMITTED"
