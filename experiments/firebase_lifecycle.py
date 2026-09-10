"""One disposable real Firebase identity, isolated loopback API/SQLite, real cleanup.

Only targets anke-sports-dev. No email/password/provider changes, existing users,
browser sessions or production databases. Credentials/tokens never enter output.
The exclusive journal contains only the generated UID and cleanup ownership marker.
"""

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
from threading import Thread
import time
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
import httpx

PROJECT = "anke-sports-dev"
PREFIX = "anke-sports-lifecycle-"


class CheckFailed(Exception):
    pass


def require(ok, label):
    if not ok:
        raise CheckFailed(label)


def private_json(path):
    require(not path.is_symlink() and path.is_file(), "credential_file_required")
    require(stat.S_IMODE(path.stat().st_mode) == 0o600, "credential_file_requires_0600")
    try:
        value = json.loads(path.read_text())
    except Exception:
        raise CheckFailed("credential_json_invalid") from None
    require(isinstance(value, dict), "credential_json_object_required")
    return value


def credentials(service_path, web_path):
    service, web = private_json(service_path), private_json(web_path)
    require(service.get("project_id") == PROJECT and web.get("projectId") == PROJECT,
            "independent_project_mismatch")
    require(service.get("type") == "service_account" and bool(service.get("private_key")),
            "service_account_required")
    require(bool(web.get("apiKey")) and web.get("authDomain") == PROJECT + ".firebaseapp.com",
            "firebase_web_config_mismatch")
    return service, web


def owns_record(record, uid, marker):
    return (re.fullmatch(PREFIX + "[0-9a-f]{32}", uid) is not None
            and record.uid == uid and record.display_name == marker
            and not record.email and not record.phone_number and not record.provider_data)


def exclusive_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


@contextmanager
def fixture(service, report):
    require(not os.getenv("WEBSITE_INSTANCE_ID") and not os.getenv("FIREBASE_AUTH_EMULATOR_HOST")
            and os.getenv("ANKE_SPORTS_ENV", "local") == "local" and "app.db" not in sys.modules,
            "fresh_local_process_without_emulator_required")
    directory = None
    try:
        with tempfile.TemporaryDirectory(prefix="anke-sports-firebase-") as directory, socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            base = f"http://localhost:{sock.getsockname()[1]}"
            os.environ.update({
                "ANKE_SPORTS_ENV": "local", "ANKE_SPORTS_LOCAL_PREVIEW": "false",
                "ANKE_SPORTS_DATABASE_URL": "sqlite:///" + directory + "/fixture.db",
                "ANKE_SPORTS_PUBLIC_URL": base, "ANKE_SPORTS_WEB_URL": base,
                "ANKE_SPORTS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
                "ANKE_SPORTS_FIREBASE_PROJECT_ID": PROJECT,
                "ANKE_SPORTS_FIREBASE_CREDENTIALS_JSON": json.dumps(service),
                "ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED": "false", "ANKE_SPORTS_YOUTUBE_PROJECT_ID": "",
                "ANKE_SPORTS_BROADCAST_CHECKS_ENABLED": "false",
                "ANKE_SPORTS_PUBLIC_FEED_SOURCE_KEYS": "[]", "ANKE_SPORTS_MAINTAINER_IDS": "[]",
                "YOUTUBE_API_KEY": "", "BALLDONTLIE_API_KEY": "", "FOOTBALL_DATA_API_KEY": "",
            })
            import firebase_admin
            from firebase_admin import credentials as certificates
            import uvicorn
            from app.db import engine
            from app.main import app

            identity = firebase_admin.initialize_app(certificates.Certificate(service),
                {"projectId": PROJECT, "httpTimeout": 20}, name="anke-sports-auth")
            server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="critical"))
            thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    require(thread.is_alive(), "fixture_api_exited")
                    time.sleep(0.05)
                require(server.started, "fixture_api_startup_timeout")
                with httpx.Client(base_url=base, headers={"Origin": base}, timeout=25,
                                  follow_redirects=False, trust_env=False) as owner:
                    yield base, owner, identity
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                engine.dispose()
                firebase_admin.delete_app(identity)
                require(not thread.is_alive(), "fixture_api_shutdown_failed")
    finally:
        if directory is not None:
            report["cleanup"]["temporary_api_and_db_removed"] = not Path(directory).exists()


def extension_grant(owner, base):
    """Issue one fixture grant through the real HTTP consent and PKCE paths."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    redirect, scopes = base + "/unused-callback", "calendar:read"
    registration = owner.post("/register", json={
        "client_name": "Firebase lifecycle fixture", "redirect_uris": [redirect],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none", "scope": scopes,
    })
    require(registration.status_code == 201, "fixture_client_registration")
    client_id = registration.json()["client_id"]
    from app.oauth import resource

    audience = resource("extension")
    authorization = owner.get("/authorize", params={
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "fixture",
        "scope": scopes, "resource": audience,
    })
    require(authorization.status_code == 302, "fixture_consent_redirect")
    pending = parse_qs(urlsplit(authorization.headers["location"]).query)["request"][0]
    decision = owner.post("/api/v1/me/connections/requests/" + pending,
                          json={"approved": True, "scopes": scopes.split()})
    require(decision.status_code == 200, "firebase_consent_authorized")
    callback = urlsplit(decision.json()["redirect_url"])
    require(callback.netloc == urlsplit(base).netloc and callback.path == "/unused-callback",
            "fixture_callback_target")
    query = parse_qs(callback.query)
    require(query["state"] == ["fixture"], "fixture_callback_state")
    issued = owner.post("/token", data={
        "client_id": client_id, "grant_type": "authorization_code", "code": query["code"][0],
        "redirect_uri": redirect, "code_verifier": verifier, "resource": audience,
    })
    require(issued.status_code == 200, "fixture_token_issued")
    return client_id, audience, issued.json()


def fresh_worker(job_id, expected_claim=True):
    # No scheduler or arbitrary jobs: dispatch this exact isolated outbox item.
    command = "from app.worker import run_one; import sys; sys.exit(0 if run_one(sys.argv[1]) else 2)"
    result = subprocess.run([sys.executable, "-c", command, job_id], capture_output=True, timeout=55)
    require(result.returncode == (0 if expected_claim else 2), "fresh_worker_process_failed")


def run(service, web, journal, report):
    from firebase_admin import auth

    run_id = secrets.token_hex(16)
    uid, marker = PREFIX + run_id, "Anke Sports disposable lifecycle " + run_id
    exclusive_json(journal, {"project_id": PROJECT, "uid": uid, "display_name": marker})
    report["test_uid"] = uid
    report["checks"] = []
    report["cleanup"] = {"remote_user_absent": False, "temporary_api_and_db_removed": False}

    def passed(label, ok=True):
        require(ok, label)
        report["checks"].append(label)
        print(json.dumps({"check": label, "passed": True}), flush=True)

    with fixture(service, report) as (base, owner, identity), httpx.Client(timeout=20, follow_redirects=False) as google:
        attempted = False
        try:
            try:
                auth.get_user(uid, app=identity)
            except auth.UserNotFoundError:
                pass
            else:
                raise CheckFailed("generated_uid_already_exists")
            attempted = True
            record = auth.create_user(uid=uid, display_name=marker, app=identity)
            passed("dedicated_remote_identity_created", owns_record(record, uid, marker))

            def login():
                token = auth.create_custom_token(uid, app=identity).decode()
                result = google.post("https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken",
                    params={"key": web["apiKey"]}, json={"token": token, "returnSecureToken": True})
                require(result.status_code == 200, "real_custom_token_exchange_failed")
                value = result.json()
                require(isinstance(value.get("idToken"), str) and isinstance(value.get("refreshToken"), str),
                        "firebase_tokens_missing")
                verified = auth.verify_id_token(value["idToken"], app=identity, check_revoked=True)
                require(verified["uid"] == uid, "firebase_returned_wrong_uid")
                return value

            def refresh(token):
                return google.post("https://securetoken.googleapis.com/v1/token",
                    params={"key": web["apiKey"]}, data={"grant_type": "refresh_token", "refresh_token": token})

            tokens = login()
            owner.headers["Authorization"] = "Bearer " + tokens["idToken"]
            claims = auth.verify_id_token(tokens["idToken"], app=identity, check_revoked=True)
            passed("real_firebase_id_verified", claims["uid"] == uid
                   and claims["firebase"]["sign_in_provider"] == "custom")
            passed("local_preview_disabled", owner.post("/api/v1/auth/local").status_code == 404)
            passed("real_identity_accepted_by_http", owner.get("/api/v1/me/calendar").status_code == 200)
            renewed = refresh(tokens["refreshToken"])
            passed("firebase_refresh_before_revocation", renewed.status_code == 200
                   and renewed.json()["user_id"] == uid)
            # Use natural time so the revocation second is later than this auth_time.
            time.sleep(max(0, min(2, int(claims["auth_time"]) + 1.2 - time.time())))
            auth.revoke_refresh_tokens(uid, app=identity)
            passed("revoked_id_rejected_by_http", owner.get("/api/v1/me/calendar").status_code == 401)
            refused = refresh(tokens["refreshToken"])
            passed("revoked_firebase_refresh_rejected", refused.status_code == 400)
            revoked_error = refused.json().get("error", {}).get("message")
            require(revoked_error in {"TOKEN_EXPIRED", "INVALID_REFRESH_TOKEN"},
                    "unexpected_revoked_refresh_error")
            report["revoked_refresh_error"] = revoked_error
            tokens = login()
            owner.headers["Authorization"] = "Bearer " + tokens["idToken"]
            state = owner.get("/api/v1/me/calendar")
            passed("fresh_auth_restores_active_account", state.status_code == 200)

            from sqlalchemy import func, select
            from icalendar import Calendar
            from app.db import Event, Feed, Job, Link, OAuthGrant, OAuthTokenRecord, Projection, SessionLocal, User
            from app.service import ensure_user
            from app.worker import run_one

            with SessionLocal() as db:
                start = datetime.now(timezone.utc) + timedelta(days=2)
                event = Event(source_key="fixture:firebase-lifecycle", competition_id="fixture:league",
                    sport="basketball", title="Firebase lifecycle synthetic event", starts_at=start.isoformat(),
                    local_date=start.date().isoformat(), provider="fixture", demo=True)
                db.add(event)
                ensure_user(db, "isolated-other-owner")
                db.commit()
                event_id = event.id
            selected = owner.put("/api/v1/events/" + event_id + "/selection",
                                 json={"state": "include", "expected_revision": state.json()["revision"]})
            passed("real_identity_calendar_write", selected.status_code == 200)
            added = owner.post("/api/v1/events/" + event_id + "/links", json={
                "url": "https://www.youtube.com/watch?v=abcdefghijk", "title": "Synthetic lifecycle link",
                "kind": "preview",
            })
            passed("real_identity_personal_link_write", added.status_code == 200)
            for _ in range(10):
                if not run_one():
                    break
            else:
                raise CheckFailed("fixture_publication_not_drained")
            address_response = owner.get("/api/v1/me/feed/address")
            require(address_response.status_code == 200, "fixture_feed_address")
            address = address_response.json()["url"]
            require(urlsplit(address).netloc == urlsplit(base).netloc, "fixture_feed_target")
            with httpx.Client(timeout=25, trust_env=False) as anonymous:
                before_feed = anonymous.get(address)
                parsed = Calendar.from_ical(before_feed.content)
                passed("real_identity_published_http_ics", before_feed.status_code == 200
                       and len(parsed.walk("VEVENT")) == 1)
                cid, audience, downstream = extension_grant(owner, base)
                bearer = {"Authorization": "Bearer " + downstream["access_token"]}
                passed("firebase_consent_downstream_access", anonymous.get(base + "/api/v1/me/calendar",
                                                                          headers=bearer).status_code == 200)
                passed("delete_requires_confirmation", owner.request("DELETE", "/api/v1/me",
                    json={"confirmed": False}).status_code == 400)
                require(owns_record(auth.get_user(uid, app=identity), uid, marker), "test_identity_changed")
                deletion = owner.request("DELETE", "/api/v1/me", json={"confirmed": True})
                passed("http_delete_queues_real_identity_cleanup", deletion.status_code == 200
                       and deletion.json()["identity_cleanup"] == "queued")
                passed("tombstone_rejects_token_before_worker", owner.get("/api/v1/me/calendar").status_code == 403)
                passed("remote_identity_still_exists_while_queued", owns_record(auth.get_user(uid, app=identity),
                                                                               uid, marker))
                passed("private_feed_revoked", anonymous.get(address).status_code == 404)
                passed("downstream_access_revoked", anonymous.get(base + "/api/v1/me/calendar",
                                                                  headers=bearer).status_code == 401)
                downstream_refresh = anonymous.post(base + "/token", data={
                    "client_id": cid, "grant_type": "refresh_token", "refresh_token": downstream["refresh_token"],
                    "resource": audience,
                })
                passed("downstream_refresh_revoked", downstream_refresh.status_code == 400)
            with SessionLocal() as db:
                cleanup_job = db.scalar(select(Job).where(Job.kind == "identity_cleanup"))
                require(cleanup_job.payload == {"user_id": uid, "firebase_project": PROJECT},
                        "cleanup_target_not_exact")
                job_id = cleanup_job.id
                user, feed = db.get(User, uid), db.scalar(select(Feed).where(Feed.owner_id == uid))
                passed("personal_sql_state_erased", user.deleted and user.config == {}
                       and feed.revoked and feed.paused and not feed.token_ciphertext and not feed.body
                       and not db.scalar(select(func.count()).select_from(Link).where(Link.owner_id == uid))
                       and not db.scalar(select(func.count()).select_from(Projection).where(Projection.feed_id == feed.id))
                       and not db.scalar(select(func.count()).select_from(OAuthGrant))
                       and not db.scalar(select(func.count()).select_from(OAuthTokenRecord)))
                passed("other_owner_and_public_event_preserved", not db.get(User, "isolated-other-owner").deleted
                       and db.get(Event, event_id) is not None)
            fresh_worker(job_id)
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                passed("fresh_worker_completed_cleanup", job.state == "done" and job.attempts == 1 and not job.error)
            try:
                auth.get_user(uid, app=identity)
            except auth.UserNotFoundError:
                passed("firebase_admin_confirms_remote_user_absent")
            else:
                raise CheckFailed("firebase_user_not_deleted")
            passed("deleted_remote_id_rejected_by_http", owner.get("/api/v1/me/calendar").status_code == 401)
            refused = refresh(tokens["refreshToken"])
            passed("deleted_remote_refresh_rejected", refused.status_code == 400)
            error = refused.json().get("error", {}).get("message")
            require(error in {"USER_NOT_FOUND", "TOKEN_EXPIRED", "INVALID_REFRESH_TOKEN"},
                    "unexpected_deleted_refresh_error")
            report["deleted_refresh_error"] = error
            fresh_worker(job_id, expected_claim=False)
            passed("completed_cleanup_not_claimed_twice")
            with SessionLocal() as db:
                repeated = Job(kind="identity_cleanup", payload={"user_id": uid, "firebase_project": PROJECT})
                db.add(repeated)
                db.commit()
                repeated_id = repeated.id
            fresh_worker(repeated_id)
            with SessionLocal() as db:
                passed("already_absent_firebase_cleanup_idempotent", db.get(Job, repeated_id).state == "done"
                       and db.get(User, uid).deleted and db.get(User, uid).config == {})
        finally:
            if attempted:
                try:
                    record = auth.get_user(uid, app=identity)
                except auth.UserNotFoundError:
                    report["cleanup"]["remote_user_absent"] = True
                else:
                    require(owns_record(record, uid, marker), "cleanup_ownership_mismatch_journal_retained")
                    auth.delete_user(uid, app=identity)
                    try:
                        auth.get_user(uid, app=identity)
                    except auth.UserNotFoundError:
                        report["cleanup"]["remote_user_absent"] = True
                    else:
                        raise CheckFailed("cleanup_not_verified_journal_retained")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-account", type=Path, required=True)
    parser.add_argument("--web-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    require(not args.output.exists(), "new_output_path_required")
    report = {"date": datetime.now(timezone.utc).isoformat(), "project_id": PROJECT, "success": False,
        "identity_provider": "real Firebase custom test identity", "storage": "temporary SQLite",
        "transport": "real loopback HTTP and Firebase HTTPS", "azure_tested": False,
        "google_browser_or_device_deletion_tested": False}
    journal = args.output.with_suffix(".cleanup.json")
    try:
        service, web = credentials(args.service_account, args.web_config)
        run(service, web, journal, report)
        report["success"] = True
    except Exception as error:
        # Only controlled check identifiers or exception class; never provider diagnostics.
        report["failure"] = str(error) if isinstance(error, CheckFailed) else type(error).__name__
    if all(report.get("cleanup", {}).get(key) for key in ["remote_user_absent", "temporary_api_and_db_removed"]):
        journal.unlink()
    report["source_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in [
        Path(__file__).relative_to(Path.cwd()), Path("app/security.py"), Path("app/privacy.py"),
        Path("app/main.py"), Path("app/worker.py"), Path("app/oauth.py"), Path("app/service.py"),
    ]}
    exclusive_json(args.output, report)
    print(json.dumps({"success": report["success"], "checks_passed": len(report.get("checks", [])),
                      "failure": report.get("failure"), "cleanup": report.get("cleanup")}), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
