from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import broadcast_rules, broadcasts, platforms
from app.config import settings
from app.db import BroadcastAudit, BroadcastRecord, User
from app.security import canonical_url, digest
from app.service import save_config
from tests.test_calendar_flow import drain, feed_snapshot, insert_event
from tests.test_oauth import authorize, exchange, register
from app.oauth import resource


def payload(ident, **extra):
    return {
        "event_id": ident,
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "title": "合成比赛官方入口（隔离测试）",
        "content_type": "official_match",
        "access": "subscription",
        "region_mode": "include",
        "regions": ["US"],
        "evidence_url": "https://www.nba.com/game/fixture-evidence",
        "evidence_note": "这只是隔离测试的合成审核证据，不是实际官方赛事",
        **extra,
    }


def setup_record(stack, monkeypatch, **extra):
    client, sessions = stack
    monkeypatch.setattr(settings(), "maintainer_ids", ["local-reviewer"])
    ident = insert_event(sessions)
    response = client.post("/api/v1/maintenance/broadcasts", json=payload(ident, **extra))
    response.raise_for_status()
    return ident, response.json()


def publish(client, record, **extra):
    response = client.post(
        f"/api/v1/maintenance/broadcasts/{record['id']}/publish",
        json={
            "expected_revision": record["revision"],
            "source_and_event_confirmed": True,
            "valid_until": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            **extra,
        },
    )
    return response


def test_draft_is_private_publication_updates_original_feed_and_suspend_removes_only_link(stack, monkeypatch):
    client, sessions = stack
    ident, record = setup_record(stack, monkeypatch)
    assert client.get(f"/api/v1/events/{ident}").json()["links"] == []
    _, _, entries = feed_snapshot(client)
    uid, sequence = str(entries[0]["UID"]), int(entries[0]["SEQUENCE"])
    assert publish(client, record, source_and_event_confirmed=False).status_code == 400
    assert (
        publish(
            client, record, valid_until=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        ).status_code
        == 400
    )
    response = publish(client, record)
    response.raise_for_status()
    approved = response.json()
    drain()
    _, published, entries = feed_snapshot(client)
    assert str(entries[0]["UID"]) == uid and int(entries[0]["SEQUENCE"]) == sequence + 1
    description = str(entries[0]["DESCRIPTION"])
    assert "需要订阅" in description and "仅限 US" in description and "官方来源核验" in description
    assert str(entries[0]["URL"]) == record["draft"]["url"]
    stale = client.post(
        f"/api/v1/maintenance/broadcasts/{record['id']}/suspend",
        json={"expected_revision": 0, "reason": "过期请求"},
    )
    assert stale.status_code == 409
    withdrawn = client.post(
        f"/api/v1/maintenance/broadcasts/{record['id']}/suspend",
        json={"expected_revision": approved["revision"], "reason": "合成撤回原因"},
    )
    withdrawn.raise_for_status()
    drain()
    _, _, entries = feed_snapshot(client)
    assert str(entries[0]["UID"]) == uid and "abcdefghijk" not in str(entries[0]["DESCRIPTION"])
    assert str(entries[0]["STATUS"]) == "CONFIRMED"


def test_edit_requires_new_review_and_original_publication_stays_visible(stack, monkeypatch):
    client, _ = stack
    ident, record = setup_record(stack, monkeypatch)
    approved = publish(client, record).json()
    changed = client.put(
        f"/api/v1/maintenance/broadcasts/{record['id']}",
        json={
            **record["draft"],
            "title": "合成新标题",
            "access": "free",
            "expected_revision": approved["revision"],
        },
    )
    changed.raise_for_status()
    link = client.get(f"/api/v1/events/{ident}").json()["links"][0]
    assert link["title"] == record["draft"]["title"] and link["access"] == "subscription"
    assert publish(client, approved).status_code == 409
    publish(client, changed.json()).raise_for_status()
    assert client.get(f"/api/v1/events/{ident}").json()["links"][0]["access"] == "free"


def test_roles_are_verified_and_oauth_cannot_escalate_to_maintainer(stack, monkeypatch):
    client, sessions = stack
    ident = insert_event(sessions)
    assert client.post("/api/v1/maintenance/broadcasts", json=payload(ident)).status_code == 403
    monkeypatch.setattr(settings(), "maintainer_ids", ["local-reviewer"])
    cid = register(client)
    query, _ = authorize(client, cid, audience=resource("extension"))
    token = exchange(client, cid, query["code"][0], resource("extension")).json()["access_token"]
    assert (
        client.post(
            "/api/v1/maintenance/broadcasts",
            json=payload(ident),
            headers={"Authorization": "Bearer " + token},
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert client.get("/api/v1/maintenance/broadcasts").status_code == 401


def test_region_rules_unknown_region_and_private_blocks_are_preserved(stack, monkeypatch):
    client, sessions = stack
    ident, record = setup_record(stack, monkeypatch, region_mode="exclude", regions=["US"])
    approved = publish(client, record).json()
    link = client.get(f"/api/v1/events/{ident}").json()["links"][0]
    assert link["broadcast"]["region_label"] == "不含 US"
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        save_config(
            db,
            user,
            {**user.config, "preferences": {**user.config["preferences"], "watch_region": "US"}},
            user.revision,
        )
        db.commit()
    assert client.get(f"/api/v1/events/{ident}").json()["links"] == []
    with sessions() as db:
        user = db.get(User, "local-reviewer")
        save_config(
            db,
            user,
            {**user.config, "preferences": {**user.config["preferences"], "watch_region": None}},
            user.revision,
        )
        db.commit()
    client.post(f"/api/v1/me/links/{record['id']}/block").raise_for_status()
    publish(client, approved).raise_for_status()
    assert client.get(f"/api/v1/events/{ident}").json()["links"] == []
    client.cookies.clear()
    assert len(client.get(f"/api/v1/events/{ident}").json()["links"]) == 1


def test_reachable_probe_is_not_playback_and_identical_probe_does_not_change_ics(stack, monkeypatch):
    client, sessions = stack
    ident, record = setup_record(stack, monkeypatch)
    publish(client, record).raise_for_status()
    drain()
    _, before, _ = feed_snapshot(client)
    monkeypatch.setattr(broadcasts, "head_probe", lambda url: "reachable")
    with sessions() as db:
        broadcasts.check_record(db, record["id"], digest(record["draft"]["url"]))
        db.commit()
    drain()
    _, after, _ = feed_snapshot(client)
    assert before.content == after.content
    link = client.get(f"/api/v1/events/{ident}").json()["links"][0]
    assert link["broadcast"]["network_status"] == "reachable" and link["broadcast"]["device_tests"] == []
    with sessions() as db:
        row = db.get(BroadcastRecord, record["id"])
        assert row.status == "published" and row.published["access"] == "subscription"
        assert db.scalar(select(BroadcastAudit).where(BroadcastAudit.action == "network_checked"))


@pytest.mark.parametrize("outcome", ["retry", "restricted", "head_unsupported"])
def test_transient_or_access_restriction_does_not_remove_approved_link(stack, monkeypatch, outcome):
    client, sessions = stack
    ident, record = setup_record(stack, monkeypatch)
    publish(client, record).raise_for_status()
    monkeypatch.setattr(broadcasts, "head_probe", lambda url: outcome)
    with sessions() as db:
        broadcasts.check_record(db, record["id"], digest(record["draft"]["url"]))
        db.commit()
    assert len(client.get(f"/api/v1/events/{ident}").json()["links"]) == 1


def test_repeated_missing_or_unsafe_result_needs_review_and_expiry_is_published(stack, monkeypatch):
    client, sessions = stack
    ident, record = setup_record(stack, monkeypatch)
    publish(client, record).raise_for_status()
    drain()
    monkeypatch.setattr(broadcasts, "head_probe", lambda url: "not_found")
    with sessions() as db:
        broadcasts.check_record(db, record["id"], digest(record["draft"]["url"]))
        db.commit()
        assert db.get(BroadcastRecord, record["id"]).status == "published"
        row = db.get(BroadcastRecord, record["id"])
        row.network_checked_at = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        db.commit()
        broadcasts.check_record(db, record["id"], digest(record["draft"]["url"]))
        db.commit()
        assert row.status == "unavailable"
    drain()
    assert client.get(f"/api/v1/events/{ident}").json()["links"] == []
    current = client.get(f"/api/v1/maintenance/broadcasts/{record['id']}").json()
    publish(client, current).raise_for_status()
    with sessions() as db:
        row = db.get(BroadcastRecord, record["id"])
        row.published = {
            **row.published,
            "valid_until": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        }
        row.expires_at = row.published["valid_until"]
        db.commit()
    broadcasts.schedule_broadcasts()
    drain()
    assert client.get(f"/api/v1/maintenance/broadcasts/{record['id']}").json()["status"] == "expired"
    assert client.get(f"/api/v1/events/{ident}").json()["links"] == []


def test_device_observation_is_specific_to_published_content_and_does_not_prove_playback(stack, monkeypatch):
    client, _ = stack
    ident, record = setup_record(stack, monkeypatch)
    approved = publish(client, record).json()
    evidence = {
        "expected_revision": approved["revision"],
        "platform_app": "合成测试App",
        "os_version": "合成OS版本",
        "calendar_client": "合成日历客户端",
        "region": "US",
        "checked_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "app_installed": False,
        "exact_content": "passed",
        "app_content": "passed",
        "playback": "not_tested",
        "conditions": "合成观察，不是实际手机记录",
        "evidence_ref": "fixture-001",
    }
    path = f"/api/v1/maintenance/broadcasts/{record['id']}/device-evidence"
    assert client.post(path, json=evidence).status_code == 422
    evidence["app_installed"] = True
    result = client.post(path, json=evidence)
    result.raise_for_status()
    observations = client.get(f"/api/v1/events/{ident}").json()["links"][0]["broadcast"]["device_tests"]
    assert observations[0]["playback"] == "not_tested" and "evidence_ref" not in observations[0]
    edited = client.put(
        f"/api/v1/maintenance/broadcasts/{record['id']}",
        json={
            **record["draft"],
            "url": "https://www.youtube.com/watch?v=ZYXWVUTSRQP",
            "expected_revision": result.json()["revision"],
        },
    ).json()
    publish(client, edited).raise_for_status()
    assert client.get(f"/api/v1/events/{ident}").json()["links"][0]["broadcast"]["device_tests"] == []


@pytest.mark.parametrize(
    "url",
    [
        "https://www.nba.com/live.m3u8",
        "https://www.nba.com/live%252em3u8",
        "https://www.nba.com/game?session=private",
        "https://www.nba.com/game?next=https://localhost",
        "https://www.youtube.com/watch?v=abcdefghijk&access_token=private",
        "https://user:private@www.nba.com/game",
        "https://127.0.0.1/game",
    ],
)
def test_all_manual_transports_reject_streams_credentials_and_redirectors(url):
    with pytest.raises(HTTPException):
        canonical_url(url)


def test_probe_pins_public_address_preserves_tls_host_and_never_follows_redirect(monkeypatch):
    requests = []
    resolve_addresses = platforms._public_ips

    class Response:
        status = 302

        def getheader(self, k, default=""):
            return "https://localhost/private" if k == "Location" else default

    class Connection:
        def __init__(self, host, address):
            requests.append((host, address))

        def request(self, *args, **kwargs):
            requests.append((args, kwargs))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(platforms, "_public_ips", lambda host: ["8.8.8.8"])
    monkeypatch.setattr(platforms, "_PinnedHTTPS", Connection)
    assert platforms.head_probe("https://www.nba.com/game/fixture") == "unsafe"
    assert requests[0] == ("www.nba.com", "8.8.8.8") and requests[1][0][0] == "HEAD" and len(requests) == 2
    assert "Authorization" not in requests[1][1]["headers"]
    monkeypatch.setattr(
        platforms.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError):
        resolve_addresses("www.nba.com")


def test_rights_matrix_blocks_wrong_region_and_labels_mobile_handoff():
    apple = "https://tv.apple.com/us/info/watch-f1"
    assert platforms.rights_cover(apple, "jolpica:f1", ["US"])
    assert not platforms.rights_cover(apple, "jolpica:f1", ["CN"])
    assert platforms.mobile_opening(apple)["mobile_opening"] == "verified_https_app_link"
    assert platforms.mobile_opening("https://fod.fujitv.co.jp/title/91di/")["mobile_opening"] == "web_handoff"
    with pytest.raises(HTTPException) as error:
        broadcast_rules.validate_rights(
            {
                "content_type": "official_match",
                "region_mode": "include",
                "regions": ["JP"],
                "url": apple,
            },
            "jolpica:f1",
        )
    assert error.value.detail["code"] == "RIGHTS_SCOPE_MISMATCH"


def test_atomic_publication_and_stale_probe_cannot_change_new_url(stack, monkeypatch):
    from app.broadcast_schemas import BroadcastDecision
    from app.db import Job, Link
    from sqlalchemy import func

    client, sessions = stack
    ident, record = setup_record(stack, monkeypatch)
    with sessions() as db:
        count = db.scalar(select(func.count()).select_from(Job))
        broadcasts.approve_record(
            db,
            "local-reviewer",
            record["id"],
            BroadcastDecision(
                expected_revision=0,
                source_and_event_confirmed=True,
                valid_until=datetime.now(timezone.utc) + timedelta(days=1),
            ),
        )
        db.flush()
        db.rollback()
        assert db.get(BroadcastRecord, record["id"]).status == "draft"
        assert db.get(Link, record["id"]).available is False
        assert db.scalar(select(func.count()).select_from(Job)) == count
        assert db.scalar(select(BroadcastAudit).where(BroadcastAudit.action == "published")) is None
    approved = publish(client, record).json()
    edited = client.put(
        f"/api/v1/maintenance/broadcasts/{record['id']}",
        json={
            **record["draft"],
            "url": "https://www.youtube.com/watch?v=ZYXWVUTSRQP",
            "expected_revision": approved["revision"],
        },
    ).json()
    publish(client, edited).raise_for_status()

    def must_not_probe(url):
        raise AssertionError("Stale job attempted a network request")

    monkeypatch.setattr(broadcasts, "head_probe", must_not_probe)
    with sessions() as db:
        broadcasts.check_record(db, record["id"], digest(record["draft"]["url"]))
        assert db.get(BroadcastRecord, record["id"]).network_status == "not_checked"


def test_pinned_tls_connection_uses_literal_ip_and_original_hostname(monkeypatch):
    seen = []

    class Socket:
        def getpeername(self):
            return ("8.8.8.8", 443)

        def close(self):
            seen.append("closed")

    class Context:
        check_hostname = True

        def wrap_socket(self, raw, server_hostname):
            seen.append(("tls", server_hostname))
            return raw

    def connect(address, timeout):
        seen.append(("connect", address, timeout))
        return Socket()

    monkeypatch.setattr(platforms.ssl, "create_default_context", lambda: Context())
    monkeypatch.setattr(platforms.socket, "create_connection", connect)
    connection = platforms._PinnedHTTPS("www.nba.com", "8.8.8.8")
    connection.connect()
    assert seen == [("connect", ("8.8.8.8", 443), 5), ("tls", "www.nba.com")]
    connection.close()


def test_programme_is_not_primary_live_url_and_shared_manual_duplicates_deliver_once(stack, monkeypatch):
    client, _ = stack
    ident, record = setup_record(
        stack, monkeypatch, content_type="programme", url="https://www.nba.com/watch/featured"
    )
    publish(client, record).raise_for_status()
    client.post(
        f"/api/v1/events/{ident}/links",
        json={"url": record["draft"]["url"], "title": "合成私人补充", "kind": "live"},
    ).raise_for_status()
    drain()
    assert len(client.get(f"/api/v1/events/{ident}").json()["links"]) == 1
    _, _, entries = feed_snapshot(client)
    assert "URL" not in entries[0]
    assert "查看官方播出信息" in str(entries[0]["DESCRIPTION"])
