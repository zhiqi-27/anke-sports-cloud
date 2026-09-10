"""Installed Codex -> synthetic consent -> MCP business calls -> local SQLite.

No model turn, persistent task, browser cookies or existing application database.
OAuth credentials use a unique Codex name and are logged out in finally blocks.
Run as a fresh process: uv run python -m experiments.codex_business --output NEW.json
"""

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import time
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
import httpx

from scripts.check_codex_discovery import (
    PRIVATE_TOOLS, PUBLIC_TOOLS, ProbeError, client, inventory, isolated_overrides, verify_isolation,
)


def require(ok, label):
    if not ok:
        raise ProbeError(label)


def local_url(value, *, base=None, path=None):
    parsed = urlsplit(value)
    require(
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and not parsed.username and not parsed.password and not parsed.fragment
        and (base is None or parsed.netloc == urlsplit(base).netloc)
        and (path is None or parsed.path == path),
        "Unexpected nonlocal URL or callback route; refusing request",
    )
    return parsed


@contextmanager
def fixture():
    require(
        not os.getenv("WEBSITE_INSTANCE_ID") and os.getenv("ANKE_SPORTS_ENV", "local") == "local"
        and "app.db" not in sys.modules,
        "Use a fresh local-only process for this experiment",
    )
    with tempfile.TemporaryDirectory(prefix="anke-sports-codex-") as directory, socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        base = f"http://localhost:{sock.getsockname()[1]}"
        values = {
            "ANKE_SPORTS_ENV": "local", "ANKE_SPORTS_LOCAL_PREVIEW": "true",
            "ANKE_SPORTS_DATABASE_URL": "sqlite:///" + directory + "/fixture.db",
            "ANKE_SPORTS_PUBLIC_URL": base, "ANKE_SPORTS_WEB_URL": base,
            "ANKE_SPORTS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "ANKE_SPORTS_FIREBASE_PROJECT_ID": "", "ANKE_SPORTS_FIREBASE_CREDENTIALS_JSON": "",
            "ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED": "false", "ANKE_SPORTS_YOUTUBE_PROJECT_ID": "",
            "ANKE_SPORTS_BROADCAST_CHECKS_ENABLED": "false",
            "ANKE_SPORTS_PUBLIC_FEED_SOURCE_KEYS": "[]", "ANKE_SPORTS_MAINTAINER_IDS": "[]",
            "YOUTUBE_API_KEY": "", "BALLDONTLIE_API_KEY": "", "FOOTBALL_DATA_API_KEY": "",
        }
        os.environ.update(values)
        import uvicorn
        from app.db import engine
        from app.main import app

        counts = Counter()

        async def observed(scope, receive, send):
            async def response(message):
                if message["type"] == "http.response.start":
                    path = scope.get("path")
                    if path in {"/mcp", "/mcp/public", "/token", "/register", "/authorize"}:
                        counts[f"{scope['method']} {path} {message['status']}"] += 1
                await send(message)
            await app(scope, receive, response)

        server = uvicorn.Server(uvicorn.Config(observed, access_log=False, log_level="critical"))
        thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                require(thread.is_alive(), "Fixture API exited during startup")
                time.sleep(0.05)
            require(server.started, "Fixture API startup timed out")
            with httpx.Client(base_url=base, headers={"Origin": base}, timeout=15,
                              follow_redirects=False, trust_env=False) as owner:
                require(owner.post("/api/v1/auth/local").status_code == 200, "Fixture login failed")
                yield base, owner, counts
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            require(not thread.is_alive(), "Fixture server did not stop")
            engine.dispose()


def authorize(rpc, name, base, owner, scopes):
    response = rpc("mcpServer/oauth/login", {"name": name, "scopes": scopes, "timeoutSecs": 60})
    url = response["authorizationUrl"]
    parsed = local_url(url, base=base, path="/authorize")
    params = parse_qs(parsed.query)
    require(params.get("code_challenge_method") == ["S256"], "Codex did not request S256")
    require(params.get("resource") == [base + "/mcp"], "Codex resource mismatch")
    require(set(params.get("scope", [""])[0].split()) == set(scopes), "Codex scope mismatch")
    registered = local_url(params["redirect_uri"][0])
    response = owner.get(url)
    require(response.status_code in {302, 303, 307}, "Authorization did not reach consent")
    pending = parse_qs(local_url(response.headers["location"], base=base, path="/connect").query)["request"][0]
    route = "/api/v1/me/connections/requests/" + pending
    preview = owner.get(route)
    require(preview.status_code == 200 and preview.json()["resource"] == base + "/mcp",
            "Consent preview failed")
    require(set(preview.json()["scopes"]) == set(scopes), "Consent scope mismatch")
    decision = owner.post(route, json={"approved": True, "scopes": scopes})
    require(decision.status_code == 200, "Consent failed")
    callback = decision.json()["redirect_url"]
    target = local_url(callback)
    require((target.netloc, target.path) == (registered.netloc, registered.path), "Callback mismatch")
    query = parse_qs(target.query)
    require(query.get("state") == params.get("state") and query.get("iss") == [base + "/"],
            "Callback state or issuer mismatch")
    # A separate cookie jar prevents sending Anke's local session to the Codex callback.
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as transport:
        result = transport.get(callback)
        require(result.status_code == 200, "Codex callback did not complete")
    for _ in range(50):
        connections = owner.get("/api/v1/me/connections").json()["items"]
        if connections:
            require(len(connections) == 1, "Unexpected fixture grants")
            require(set(connections[0]["scopes"]) == set(scopes), "Granted scope mismatch")
            return connections[0]["id"]
        time.sleep(0.05)
    raise ProbeError("Codex token exchange did not produce a grant")


@contextmanager
def codex_session(binary, base, owner, *, scopes=None):
    name = "anke_sports_fixture_" + secrets.token_hex(8)
    url = base + ("/mcp" if scopes else "/mcp/public")
    overrides = isolated_overrides(binary, name, url)
    grant_id = None
    cleanup = {"credentials_cleared": False}
    try:
        with client(binary, overrides) as rpc:
            verify_isolation(rpc, name)
            inventory(rpc, name)
            if scopes:
                grant_id = authorize(rpc, name, base, owner, scopes)
            ephemeral = rpc("thread/start", {"ephemeral": True, "cwd": str(Path.cwd())})["thread"]
            require(ephemeral["ephemeral"] and ephemeral["path"] is None, "Codex context was not ephemeral")
            thread_id = ephemeral["id"]
            row = inventory(rpc, name, thread_id)
            require({t["name"] for t in row["tools"].values()} == (PRIVATE_TOOLS if scopes else PUBLIC_TOOLS),
                    "Unexpected Codex tool inventory")

            def call(tool, arguments=None, *, error=None):
                result = rpc("mcpServer/tool/call", {
                    "threadId": thread_id, "server": name, "tool": tool, "arguments": arguments or {},
                })
                if error:
                    require(result.get("isError") is True and error in json.dumps(result.get("content", [])),
                            "Expected error absent from " + tool)
                    return None
                require(not result.get("isError"), "Codex business call failed: " + tool)
                return result["structuredContent"]

            yield call, grant_id, cleanup
    finally:
        if scopes:
            # This owner cookie belongs only to the ephemeral database. Revoke even after partial login.
            connections = owner.get("/api/v1/me/connections").json()["items"]
            for grant in connections:
                require(owner.delete("/api/v1/me/connections/" + grant["id"]).status_code == 200,
                        "Fixture grant cleanup failed")
            args = [binary]
            for override in overrides:
                args.extend(["-c", override])
            result = subprocess.run(args + ["mcp", "logout", name], capture_output=True, timeout=15)
            require(result.returncode == 0, "Could not clear unique Codex fixture credentials")
            with client(binary, overrides) as rpc:
                verify_isolation(rpc, name)
                row = inventory(rpc, name)
                require(row["authStatus"] == "notLoggedIn" and not row["tools"],
                        "Codex fixture credential cleanup was not verified")
        cleanup["credentials_cleared"] = True


def run(binary):
    checks = []

    def passed(label, ok=True):
        require(ok, label)
        checks.append(label)
        print(json.dumps({"check": label, "passed": True}), flush=True)

    with fixture() as (base, owner, counts):
        from sqlalchemy import select
        from app.db import Event, Feed, Job, OAuthTokenRecord, SessionLocal, User
        from app.worker import run_one

        with SessionLocal() as db:
            event = db.scalar(select(Event).where(Event.competition_id == "demo:nba").order_by(Event.starts_at))
            event_id, source_key = event.id, event.source_key
        current = datetime.now(timezone.utc)
        period = {"from_time": (current - timedelta(days=30)).isoformat(),
                  "to_time": (current + timedelta(days=60)).isoformat(), "dataset": "demo", "limit": 2}
        with codex_session(binary, base, owner) as (call, _, _):
            sources = call("search_sources", {"dataset": "demo", "q": "NBA", "limit": 5})
            passed("public_source_query", sources["items"][0]["id"] == "demo:nba")
            first = call("get_schedule", period)
            second = call("get_schedule", {**period, "cursor": first["next_cursor"]})
            passed("public_cursor_pagination", len(first["items"]) == 2 and len(second["items"]) == 2
                   and not {e["id"] for e in first["items"]} & {e["id"] for e in second["items"]})
            passed("public_event_read", call("get_event", {"event_id": event_id})["demo"])
        with codex_session(binary, base, owner, scopes=["calendar:read"]) as (call, grant_id, cleanup):
            before = call("get_my_calendar")
            call("update_follows", {"add": [], "remove": [], "expected_revision": before["revision"],
                                   "idempotency_key": "readonly-attempt"}, error="INSUFFICIENT_SCOPE")
            passed("readonly_write_denied", call("get_my_calendar")["revision"] == before["revision"])
            call("get_calendar_feed", error="INSUFFICIENT_SCOPE")
            passed("readonly_feed_secret_denied")
        passed("readonly_credentials_cleared", cleanup["credentials_cleared"])
        with codex_session(binary, base, owner, scopes=["calendar:read", "calendar:write"]) as (call, grant_id, cleanup):
            original = call("get_my_calendar")
            follows = {"add": [{"type": "event", "source_key": source_key}], "remove": [],
                       "expected_revision": original["revision"], "idempotency_key": "codex-follow-01"}
            changed = call("update_follows", follows)
            passed("follow_write", changed["revision"] == original["revision"] + 1)
            passed("idempotent_follow_retry", call("update_follows", follows) == changed)
            call("update_follows", {**follows, "remove": [source_key]}, error="IDEMPOTENCY_CONFLICT")
            passed("changed_payload_same_key_rejected")
            call("get_my_calendar", {"userId": "synthetic-other-owner"}, error="INVALID_INPUT")
            passed("caller_identity_spoof_rejected")
            call("get_calendar_feed", error="INSUFFICIENT_SCOPE")
            passed("write_scope_does_not_reveal_feed")
            passed("private_followed_schedule", len(call("get_schedule", {**period, "followed": True})["items"]) == 1)

            def publish():
                for _ in range(100):
                    if not run_one():
                        break
                with SessionLocal() as db:
                    require(not list(db.scalars(select(Job).where(Job.state != "done"))), "Projection did not finish")
                    user = db.get(User, "local-reviewer")
                    feed = db.scalar(select(Feed).where(Feed.owner_id == user.id))
                    body, etag = feed.body, feed.etag
                # Read-only secret from this synthetic owner's HTTP session stays
                # in memory. It is never requested through the Codex connection.
                url = owner.get("/api/v1/me/feed/address").json()["url"]
                local_url(url, base=base)
                published = owner.get(url)
                require(published.status_code == 200 and published.text == body,
                        "Actual HTTP Feed differs from published projection")
                unchanged = owner.get(url, headers={"If-None-Match": published.headers["etag"]})
                require(unchanged.status_code == 304 and not unchanged.content, "Feed conditional read failed")
                return body, etag

            from icalendar import Calendar
            body_before, _ = publish()
            calendar_before = Calendar.from_ical(body_before).walk("VEVENT")
            data = {"url": "https://www.youtube.com/watch?v=LocalVideo1", "title": "合成 Codex 原链接", "kind": "preview"}
            attached = call("attach_event_link", {"event_id": event_id, "data": data,
                                                  "idempotency_key": "codex-link-01"})
            replay = owner.post(f"/api/v1/events/{event_id}/links", json=data,
                                headers={"Idempotency-Key": "codex-link-01"})
            passed("cross_transport_link_retry", replay.status_code == 200 and replay.json() == attached)
            body_added, _ = publish()
            calendar_added = Calendar.from_ical(body_added).walk("VEVENT")
            passed("link_publish_preserves_uid", len(calendar_added) == len(calendar_before) == 1
                   and calendar_added[0]["UID"] == calendar_before[0]["UID"]
                   and calendar_added[0]["SEQUENCE"] > calendar_before[0]["SEQUENCE"]
                   and "LocalVideo1" in str(calendar_added[0]["DESCRIPTION"]))
            passed("link_removed", call("remove_event_link", {"link_id": attached["id"],
                                                               "idempotency_key": "codex-remove-01"})["blocked"])
            again = call("attach_event_link", {"event_id": event_id, "data": data,
                                               "idempotency_key": "codex-link-02"})
            passed("removed_link_stays_blocked", not again["event"]["links"])
            body_removed, etag_removed = publish()
            removed = Calendar.from_ical(body_removed).walk("VEVENT")[0]
            passed("blocked_link_removed_from_feed", removed["UID"] == calendar_before[0]["UID"]
                   and "LocalVideo1" not in str(removed["DESCRIPTION"]))
            passed("identical_publish_stable", publish() == (body_removed, etag_removed))
            passed("published_feed_http_200_and_304")
            config = call("export_config")
            passed("export_without_credentials", not any(x in json.dumps(config) for x in
                   ("as_at_", "as_rt_", "/feeds/", "token", "user_id")))
            draft = {"config": config, "mode": "replace", "dry_run": True,
                     "expected_revision": call("get_my_calendar")["revision"]}
            preview = call("import_config", {"data": draft, "idempotency_key": "codex-import-01"})
            passed("import_preview_readonly", not preview["applied"]
                   and call("get_my_calendar")["revision"] == draft["expected_revision"])
            call("import_config", {"data": {**draft, "dry_run": False},
                                   "idempotency_key": "codex-import-02"}, error="PREVIEW_REQUIRED")
            applied = call("import_config", {"data": {**draft, "dry_run": False, "confirmation": preview["confirmation"]},
                                              "idempotency_key": "codex-import-02"})
            passed("import_requires_exact_preview", applied["applied"])

            # Controlled server-side expiration probes the client's 401 recovery, not elapsed 15 minutes.
            with SessionLocal() as db:
                refresh_before = set(db.scalars(select(OAuthTokenRecord.token_hash).where(
                    OAuthTokenRecord.grant_id == grant_id, OAuthTokenRecord.kind == "refresh")))
                for token in db.scalars(select(OAuthTokenRecord).where(
                    OAuthTokenRecord.grant_id == grant_id, OAuthTokenRecord.kind == "access")):
                    token.expires_at = int(time.time()) - 1
                db.commit()
            call("get_my_calendar")
            with SessionLocal() as db:
                used = set(db.scalars(select(OAuthTokenRecord.token_hash).where(
                    OAuthTokenRecord.grant_id == grant_id, OAuthTokenRecord.kind == "refresh",
                    OAuthTokenRecord.used.is_(True))))
                passed("controlled_expiry_refresh_rotation", bool(refresh_before & used))
            denied_before = counts["POST /mcp 401"]
            require(owner.delete("/api/v1/me/connections/" + grant_id).status_code == 200, "Revoke failed")
            try:
                call("get_my_calendar")
            except ProbeError:
                passed("revoked_client_call_rejected", counts["POST /mcp 401"] > denied_before
                       and owner.get("/api/v1/health").status_code == 200)
            else:
                raise ProbeError("Revoked client still read private data")
        passed("write_credentials_cleared", cleanup["credentials_cleared"])
        require(not owner.get("/api/v1/me/connections").json()["items"], "Active test grants remain")
        require(owner.post("/api/v1/auth/logout").status_code == 200, "Fixture session cleanup failed")
        observations = dict(sorted(counts.items()))
    return {
        "passed": True, "checks": checks, "client": subprocess.check_output([binary, "--version"], text=True).strip(),
        "transport": "installed Codex app-server mcpServer/tool/call -> Streamable HTTP",
        "dataset": "synthetic seed; isolated temporary SQLite; explicit local identity",
        "http_observations": observations, "temporary_database_removed": True,
        "unrelated_mcp_runtimes_disabled": True, "model_turns_started": False,
        "persistent_threads_created": False, "configuration_files_changed": False,
        "limitations": ["No model-driven tool selection", "No elapsed-time refresh or seven-day expiry test",
                        "No Firebase, HTTPS, Azure, MySQL or real YouTube call in this experiment"],
        "source_sha256": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in
                          ["scripts/check_codex_discovery.py", "experiments/codex_business.py", "app/mcp_server.py", "app/oauth.py"]},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default=shutil.which("codex"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.codex or args.output.exists():
        parser.error("Installed Codex and a new output path are required")
    logging.disable(logging.CRITICAL)
    try:
        result = run(args.codex)
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc) if isinstance(exc, ProbeError)
                          else "Local experiment failed; raw details withheld"}), flush=True)
        return 1
    with args.output.open("x") as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
        target.write("\n")
    print(json.dumps({"passed": True, "check_count": len(result["checks"]), "cleanup": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
