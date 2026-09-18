from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from app.broadcast_schemas import BroadcastDraft, BroadcastDecision, BroadcastAction
from app.schemas import AddLink
from tests.test_document_runtime import document_stack as document_stack, drain, follow


def test_review_publish_region_and_withdraw_in_both_feeds(document_stack):
    client, rt, events, _ = document_stack
    client.post("/api/v1/auth/local")
    follow(client)
    draft = BroadcastDraft(
        event_id=events[0]["id"],
        url="https://www.formula1.com/en/latest/article/test-event",
        title="Fixture broadcast",
        content_type="programme",
        access="subscription",
        region_mode="exclude",
        regions=["CN"],
        evidence_url="https://www.formula1.com/en/latest/article/test-evidence",
        evidence_note="Synthetic test evidence only; no real broadcast claim.",
    )
    created = rt.broadcasts.create("test-maintainer", draft)
    assert rt.broadcasts.selected(rt.catalog.capture().event(events[0]["id"]), None) == []
    assert client.post("/api/v1/maintenance/broadcasts", json=draft.model_dump()).status_code == 403
    published = rt.broadcasts.change(
        "test-maintainer",
        created["id"],
        "publish",
        BroadcastDecision(
            expected_revision=0,
            source_and_event_confirmed=True,
            valid_until=datetime.now(timezone.utc) + timedelta(days=1),
        ),
    )
    drain(rt)
    public = client.get("/api/v1/public-feed", params={"source_key": "fixture:league"}).json()["url"]
    private = client.get("/api/v1/me/feed/address").json()["url"]
    assert "test-event" in client.get(public).text
    assert "test-event" in client.get(private).text
    user = rt.accounts.active("local-reviewer")["payload"]
    user["config"]["preferences"]["watch_region"] = "CN"
    assert rt.broadcasts.selected(rt.catalog.capture().event(events[0]["id"]), user) == []
    rt.broadcasts.change(
        "test-maintainer",
        created["id"],
        "suspend",
        BroadcastAction(expected_revision=published["revision"], reason="Fixture withdrawal"),
    )
    drain(rt)
    assert "test-event" not in client.get(public).text
    assert "test-event" not in client.get(private).text


def test_personal_block_of_public_link_survives_republication(document_stack):
    client, rt, events, _ = document_stack
    client.post("/api/v1/auth/local")
    created = rt.broadcasts.create(
        "test",
        BroadcastDraft(
            event_id=events[0]["id"],
            url="https://tv.apple.com/us/sporting-event/fixture",
            title="Fixture",
            content_type="programme",
            region_mode="include",
            regions=["US"],
            evidence_url="https://tv.apple.com/us/info/watch-f1",
            evidence_note="Synthetic evidence for isolated regression only",
        ),
    )
    decision = BroadcastDecision(
        expected_revision=0,
        source_and_event_confirmed=True,
        valid_until=datetime.now(timezone.utc) + timedelta(days=1),
    )
    published = rt.broadcasts.change("test", created["id"], "publish", decision)
    result = client.post("/api/v1/me/links/" + created["id"] + "/block")
    assert result.status_code == 200, result.text
    user = rt.accounts.active("local-reviewer")["payload"]
    assert rt.broadcasts.selected(rt.catalog.capture().event(events[0]["id"]), user) == []
    decision.expected_revision = published["revision"]
    rt.broadcasts.change("test", created["id"], "publish", decision)
    assert rt.broadcasts.selected(rt.catalog.capture().event(events[0]["id"]), user) == []
    assert len(rt.broadcasts.selected(rt.catalog.capture().event(events[0]["id"]), None)) == 1


def test_region_and_league_preference_keeps_one_broadcast_in_personal_calendar(document_stack):
    client, rt, events, _ = document_stack
    client.post("/api/v1/auth/local").raise_for_status()
    follow(client)
    event = rt.catalog.capture().event(events[0]["id"])
    records = []
    for url, title in [
        ("https://tv.apple.com/us/sporting-event/fixture", "Apple fixture"),
        ("https://www.peacocktv.com/watch/fixture", "Peacock fixture"),
    ]:
        record = rt.broadcasts.create(
            "test",
            BroadcastDraft(
                event_id=event.id,
                url=url,
                title=title,
                content_type="programme",
                region_mode="include",
                regions=["US"],
                evidence_url=url,
                evidence_note="Synthetic preference regression only",
            ),
        )
        rt.broadcasts.change(
            "test",
            record["id"],
            "publish",
            BroadcastDecision(
                expected_revision=0,
                source_and_event_confirmed=True,
                valid_until=datetime.now(timezone.utc) + timedelta(days=1),
            ),
        )
        records.append(record)
    account = client.get("/api/v1/me/calendar").json()
    preferences = {
        **account["config"]["preferences"],
        "watch_region": "US",
        "broadcast_platforms": {f"US:{event.competition_id}": "peacock"},
    }
    response = client.patch(
        "/api/v1/me/preferences",
        json={"preferences": preferences, "expected_revision": account["revision"]},
    )
    assert response.status_code == 200, response.text
    drain(rt)
    detail = client.get(f"/api/v1/events/{event.id}").json()
    assert len([link for link in detail["links"] if link["broadcast"]]) == 2
    assert detail["links"][0]["broadcast"]["platform_id"] == "peacock"
    address = client.get("/api/v1/me/feed/address").json()["url"]
    calendar = client.get(address).text
    assert "Peacock fixture" in calendar
    assert "Apple fixture" not in calendar


def test_expiry_is_withdrawn_even_when_network_checks_disabled(document_stack):
    from app.document_store import Write, clean

    client, rt, events, _ = document_stack
    draft = BroadcastDraft(
        event_id=events[0]["id"],
        url="https://m.miguvideo.com/fixture/match",
        title="Fixture",
        content_type="programme",
        region_mode="include",
        regions=["CN"],
        evidence_url="https://www.premierleague.com/en/media/broadcasters",
        evidence_note="Synthetic expiry regression only",
    )
    record = rt.broadcasts.create("test", draft)
    rt.broadcasts.change(
        "test",
        record["id"],
        "publish",
        BroadcastDecision(
            expected_revision=0,
            source_and_event_confirmed=True,
            valid_until=datetime.now(timezone.utc) + timedelta(days=1),
        ),
    )
    drain(rt)
    old = rt.broadcasts.get(record["id"])
    expired = clean(old)
    expired["payload"]["published"]["valid_until"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    rt.store.batch("state", rt.broadcasts.pk, [Write("replace", old["id"], expired, old["_etag"])])
    rt.broadcasts.schedule()
    drain(rt)
    assert rt.broadcasts.get(record["id"])["payload"]["status"] == "expired"
    address = client.get("/api/v1/public-feed", params={"source_key": "fixture:league"}).json()["url"]
    assert "miguvideo" not in client.get(address).text


def test_document_calendar_uses_selected_product_and_manual_link_overrides_it(document_stack):
    client, rt, events, _ = document_stack
    source_event = rt.catalog.capture().event(events[0]["id"])
    event = SimpleNamespace(
        id=source_event.id,
        source_key=source_event.source_key,
        competition_id="balldontlie:nba",
        status="scheduled",
    )
    payload = {
        "user_id": "local-reviewer",
        "config": {
            "preferences": {
                "watch_region": "US",
                "broadcast_platforms": {"US:balldontlie:nba": "peacock"},
            },
            "link_overrides": [],
        },
    }
    automatic = rt.broadcasts.selected(event, payload)
    assert automatic[0]["url"] == "https://www.peacocktv.com/sports/nba"
    assert automatic[0]["broadcast"]["content_label"] == "官方直播产品"

    manual = SimpleNamespace(
        id="manual-link",
        event_id=event.id,
        owner_id="local-reviewer",
        url="https://www.nba.com/game/manual-link",
        title="我的直播入口",
        kind="live",
        platform="nba.com",
        origin="manual",
        access="unknown",
        regions=[],
        available=True,
        created_at="2026-09-18T00:00:00+00:00",
    )
    selected = rt.content.selected(event, payload, rows=[manual])
    assert [(link["origin"], link["url"]) for link in selected] == [
        ("manual", "https://www.nba.com/game/manual-link")
    ]

    client.post("/api/v1/auth/local").raise_for_status()
    foreign_url = "https://community.example/live/fixture"
    attached = rt.content.attach(
        "local-reviewer",
        event.id,
        AddLink(url=foreign_url, title="社区直播入口"),
        "document-foreign-platform",
    )
    assert attached["event"]["links"][0]["url"] == foreign_url
    assert attached["event"]["links"][0]["kind"] == "live"
    rt.accounts.ensure("other-user")
    other = rt.accounts.active("other-user")["payload"]
    assert foreign_url not in {link["url"] for link in rt.content.selected(event, other)}
