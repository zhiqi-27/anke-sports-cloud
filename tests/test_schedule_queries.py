"""Bounded hydration must preserve chronological, ownership and publication rules."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import event as sql_event

from app.actions import get_schedule
from app.db import Event, Link, User
from app.security import digest
from tests.test_broadcasts import publish, setup_record


def add_event(db, ident, start="2026-09-10T09:00:00Z", **extra):
    data = dict(
        id=ident,
        source_key=f"query:{ident}",
        competition_id="query:league",
        sport="basketball",
        title="合成比赛",
        starts_at=start,
        local_date=start[:10] if start else None,
        participants=[],
        provider="test",
        demo=True,
    )
    db.add(Event(**(data | extra)))


def query(db, **extra):
    args = dict(from_="2026-09-10T00:00:00Z", to="2026-09-11T00:00:00Z", dataset="demo")
    return get_schedule(db, **(args | extra))


def test_absolute_time_order_half_open_edges_and_date_only_pagination(stack):
    _, sessions = stack
    with sessions() as db:
        # All three strings mislead a lexical sort, including extreme valid offsets.
        add_event(db, "first", "2026-09-10T23:59:01+23:59")  # 00:00:01Z
        add_event(db, "middle", "2026-09-09T02:01:00-23:59")  # 02:00:00Z
        add_event(db, "last", "2026-09-11T01:00:00+14:00")  # 11:00:00Z
        add_event(db, "at-lower", "2026-09-09T16:00:00-08:00")
        add_event(db, "at-upper", "2026-09-11T00:00:00Z")
        add_event(db, "too-early", "2026-09-09T23:59:59.999999Z")
        add_event(db, "date", None, local_date="2026-09-10", time_precision="date_only")
        add_event(db, "date-upper", None, local_date="2026-09-11", time_precision="date_only")
        add_event(db, "undated", None, time_precision="unknown")
        db.commit()
        ids, cursor = [], None
        while True:
            page = query(db, limit=2, cursor=cursor)
            ids.extend(row["id"] for row in page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert ids == ["date", "at-lower", "first", "middle", "last"]
        dated = query(db)["items"][0]
        assert dated["starts_at"] is None and dated["time_precision"] == "date_only"


def test_repeated_hour_sorts_by_instant_instead_of_wall_clock(stack):
    _, sessions = stack
    with sessions() as db:
        add_event(db, "earlier", "2026-11-01T01:45:00-04:00")
        add_event(db, "later", "2026-11-01T01:15:00-05:00")
        db.commit()
        result = query(db, from_="2026-11-01T00:00:00-04:00", to="2026-11-02T00:00:00-05:00")
        assert [row["id"] for row in result["items"]] == ["earlier", "later"]


@pytest.mark.parametrize("text,expected", [("STRASSE", ["unicode"]), ("%_", ["literal"]), ("missing", [])])
def test_search_preserves_unicode_casefold_and_literal_sql_wildcards(stack, text, expected):
    _, sessions = stack
    with sessions() as db:
        add_event(db, "unicode", title="Straße 合成比赛")
        add_event(db, "literal", title="合成 100%_比赛")
        add_event(db, "ordinary")
        db.commit()
        assert [row["id"] for row in query(db, q=text)["items"]] == expected


def test_public_schedule_filters_one_competition_or_team(stack):
    _, sessions = stack
    with sessions() as db:
        add_event(
            db,
            "selected-team",
            competition_id="query:nba",
            participants=[
                {"id": "query:team:1", "name": "一队", "short_name": "ONE", "color": "#111111"},
                {"id": "query:team:2", "name": "二队", "short_name": "TWO", "color": "#222222"},
            ],
        )
        add_event(db, "same-league", competition_id="query:nba")
        add_event(db, "other-league", competition_id="query:f1", sport="racing")
        db.commit()

        assert [row["id"] for row in query(db, source_id="query:nba")["items"]] == [
            "same-league",
            "selected-team",
        ]
        assert [row["id"] for row in query(db, source_id="query:team:1")["items"]] == ["selected-team"]
        assert query(db, source_id="query:team:missing")["items"] == []


def test_envelope_handles_datetime_limits_and_empty_start_date_placeholder(stack):
    _, sessions = stack
    with sessions() as db:
        add_event(db, "empty", "", local_date="2026-09-10", time_precision="date_only")
        db.commit()
        assert query(db)["items"][0]["id"] == "empty"
        for first, last in [
            ("0001-01-01T00:00:00Z", "0001-01-02T00:00:00Z"),
            ("9999-12-30T00:00:00Z", "9999-12-31T23:59:59Z"),
        ]:
            assert not query(db, from_=first, to=last)["items"]
        with pytest.raises(HTTPException) as error:
            query(db, from_="0001-01-01T00:00:00+14:00", to="0001-01-02T00:00:00Z")
        assert error.value.status_code == 400


def test_only_page_events_are_hydrated_and_link_queries_do_not_scale_with_schedule(stack):
    _, sessions = stack
    with sessions() as db:
        for i in range(700):
            add_event(db, f"{i:04d}")
        for i in range(700):
            add_event(db, f"old-{i}", "2024-01-01T00:00:00Z")
        db.commit()
    statements, hydrated = [], []
    engine = sessions.kw["bind"]

    def statement(*args):
        statements.append(args[2])

    def loaded(target, _):
        hydrated.append(target.id)

    sql_event.listen(engine, "before_cursor_execute", statement)
    sql_event.listen(Event, "load", loaded)
    try:
        with sessions() as db:
            result = query(db, limit=7)
        assert len(result["items"]) == 7 and result["next_cursor"]
        assert len(hydrated) == 7 and not any(ident.startswith("old-") for ident in hydrated)
        assert len(statements) < 10  # Prevent per-candidate and per-page-item SQL regressions.
    finally:
        sql_event.remove(engine, "before_cursor_execute", statement)
        sql_event.remove(Event, "load", loaded)


def test_follow_union_explicit_include_exclude_and_stale_cursor(stack):
    _, sessions = stack
    with sessions() as db:
        for i in range(5):
            add_event(db, str(i), competition_id="query:league" if i < 3 else "other")
        user = db.get(User, "local-reviewer")
        user.config = {
            **user.config,
            "follows": [{"type": "competition", "source_key": "query:league"}],
            "event_overrides": [
                {"event_key": "query:1", "state": "exclude"},
                {"event_key": "query:4", "state": "include"},
            ],
        }
        db.commit()
        first = query(db, followed=True, user=user, limit=1)
        assert first["items"][0]["id"] == "0"
        assert [row["id"] for row in query(db, followed=True, user=user)["items"]] == ["0", "2", "4"]
        # A later page changing still invalidates the first page's cursor.
        db.get(Event, "4").updated_at = "2026-09-10T01:00:00Z"
        db.commit()
        with pytest.raises(HTTPException) as error:
            query(db, followed=True, user=user, cursor=first["next_cursor"])
        assert error.value.status_code == 409


def test_page_links_keep_public_review_blocks_pins_regions_and_owner_boundary(stack, monkeypatch):
    client, sessions = stack
    ident, record = setup_record(stack, monkeypatch)
    publish(client, record).raise_for_status()
    with sessions() as db:
        for owner, key, origin in [
            ("other", "other", "manual"),
            ("public", "unreviewed", "official"),
            ("local-reviewer", "pinned", "automatic"),
        ]:
            url = "https://www.youtube.com/watch?v=" + key.ljust(11, "_")
            db.add(
                Link(
                    id=key,
                    owner_id=owner,
                    event_id=ident,
                    url=url,
                    url_hash=digest(url),
                    title="合成链接",
                    platform="YouTube",
                    kind="preview",
                    origin=origin,
                )
            )
        user = db.get(User, "local-reviewer")
        event = db.get(Event, ident)
        user.config = {
            **user.config,
            "link_overrides": [
                {"event_key": event.source_key, "url": record["draft"]["url"], "state": "block"},
                {
                    "event_key": event.source_key,
                    "url": "https://www.youtube.com/watch?v=pinned_____",
                    "state": "pin",
                },
            ],
        }
        db.commit()
        start = datetime.fromisoformat(event.starts_at)
        args = dict(from_=start.isoformat(), to=(start + timedelta(days=1)).isoformat())
        private = query(db, user=user, **args)["items"][0]
        assert [link["id"] for link in private["links"]] == ["pinned"]
        assert private["links"][0]["pinned"]
        public = query(db, **args)["items"][0]
        assert [link["id"] for link in public["links"]] == [record["id"]]
        assert public["links"][0]["broadcast"]["access_label"] == "需要订阅"
        user.config = {
            **user.config,
            "link_overrides": [],
            "preferences": {**user.config["preferences"], "watch_region": "GB"},
        }
        db.commit()
        assert query(db, user=user, **args)["items"][0]["links"] == []


def test_changed_selected_event_between_candidate_read_and_hydration_is_rejected(disk_stack):
    sessions, ident = disk_stack
    engine = sessions.kw["bind"]
    changed = False

    def change_after_candidates(connection, cursor, statement, *_):
        nonlocal changed
        if not changed and statement.startswith("SELECT events.id, events.starts_at"):
            changed = True
            with sessions() as writer:
                event = writer.get(Event, ident)
                event.updated_at = "2099-01-01T00:00:00Z"
                writer.commit()

    sql_event.listen(engine, "after_cursor_execute", change_after_candidates)
    try:
        with sessions() as db:
            instant = datetime.now(timezone.utc)
            with pytest.raises(HTTPException) as error:
                get_schedule(
                    db, instant.isoformat(), (instant + timedelta(days=5)).isoformat(), dataset="demo"
                )
            assert changed and error.value.status_code == 409
    finally:
        sql_event.remove(engine, "after_cursor_execute", change_after_candidates)
