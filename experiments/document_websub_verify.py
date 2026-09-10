"""Temporary API and worker processes; synthetic Hub uses actual loopback callbacks."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.fernet import Fernet
from icalendar import Calendar

CHANNEL, VIDEO = "UC" + "w" * 22, "websub00001"
PUBLIC = "https://websub-fixture.example"
SOURCES = [
    "app/document_api.py",
    "app/document_channels.py",
    "app/document_creators.py",
    "app/document_runtime.py",
    "app/document_worker.py",
    "app/document_websub.py",
    "app/document_matches.py",
    "app/websub_rules.py",
    "experiments/document_websub_verify.py",
]


def require(ok, label):
    if not ok:
        raise RuntimeError(label)


def write_json(path, value):
    temp = path.with_suffix(".writing")
    temp.write_text(json.dumps(value, ensure_ascii=False))
    temp.chmod(0o600)
    temp.replace(path)


def trace(name):
    with (Path(os.environ["ANKE_WEBSUB_FIXTURE_DIR"]) / "trace").open("a") as stream:
        stream.write(name + "\n")


def install_fixture():
    require(
        not os.getenv("WEBSITE_INSTANCE_ID")
        and os.getenv("ANKE_SPORTS_ENV") == "local"
        and os.getenv("ANKE_SPORTS_YOUTUBE_PROJECT_ID") == "synthetic-websub-proof",
        "LOCAL_FIXTURE_REQUIRED",
    )
    base = os.environ["ANKE_WEBSUB_FIXTURE_BASE"]
    require(base.startswith("http://127.0.0.1:"), "LOOPBACK_REQUIRED")
    folder = Path(os.environ["ANKE_WEBSUB_FIXTURE_DIR"])
    real_client = httpx.Client

    def respond(request):
        if str(request.url) == "https://pubsubhubbub.appspot.com/subscribe" and request.method == "POST":
            data = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            require(
                data["hub.callback"].startswith(PUBLIC + "/webhooks/youtube/"), "FIXTURE_CALLBACK_REQUIRED"
            )
            require(data["hub.topic"].endswith("channel_id=" + CHANNEL), "FIXTURE_TOPIC_REQUIRED")
            query = {
                "hub.mode": data["hub.mode"],
                "hub.topic": data["hub.topic"],
                "hub.challenge": "synthetic-" + str(time.monotonic_ns()),
                "hub.lease_seconds": "432000",
            }
            with real_client(timeout=10) as callback:
                response = callback.get(base + urlsplit(data["hub.callback"]).path, params=query)
            require(
                response.status_code == 200 and response.text == query["hub.challenge"], "HUB_CALLBACK_FAILED"
            )
            write_json(folder / "hub.json", data)
            trace("hub:" + data["hub.mode"])
            return httpx.Response(202)
        require(
            request.url.host == "www.googleapis.com"
            and request.headers.get("x-goog-api-key") == "synthetic-no-network",
            "OUTBOUND_FORBIDDEN",
        )
        endpoint = request.url.path.rsplit("/", 1)[-1]
        fixture = json.loads((folder / "fixture.json").read_text())
        trace("youtube:" + endpoint)
        if endpoint == "channels":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": CHANNEL,
                            "snippet": {"title": "Synthetic WebSub fixture"},
                            "contentDetails": {"relatedPlaylists": {"uploads": "UUwebsubFixture"}},
                        }
                    ]
                },
            )
        if endpoint == "playlistItems":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"contentDetails": {"videoId": VIDEO, "videoPublishedAt": fixture["published_at"]}}
                    ]
                },
            )
        require(endpoint == "videos" and request.url.params.get("id") == VIDEO, "FIXTURE_VIDEO_REQUIRED")
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": VIDEO,
                        "snippet": {
                            "channelId": CHANNEL,
                            "title": fixture["title"],
                            "description": "Synthetic test only",
                            "publishedAt": fixture["published_at"],
                        },
                        "status": {"privacyStatus": "public"},
                    }
                ]
            },
        )

    httpx.Client = lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)


def child(mode):
    install_fixture()
    if mode == "worker":
        trace("worker:boot")
        from app.document_worker import main

        return main([])
    from app.config import settings
    from app.document_catalog import Catalog
    from app.document_store import LocalDocumentStore
    from app.main import app
    import uvicorn

    require("app.db" not in sys.modules, "SQL_RUNTIME_FORBIDDEN")
    cfg = settings()
    fixture = json.loads((Path(os.environ["ANKE_WEBSUB_FIXTURE_DIR"]) / "fixture.json").read_text())
    Catalog(LocalDocumentStore(cfg.document_local_path)).publish(
        "websub-proof", fixture["events"], fixture["sources"], expected_revision=0, complete=True
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=urlsplit(os.environ["ANKE_WEBSUB_FIXTURE_BASE"]).port,
        log_level="warning",
        access_log=False,
    )
    return 0


def stop(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def wait_for(predicate, label, processes):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        require(all(p.poll() is None for p in processes), "FIXTURE_PROCESS_EXITED")
        try:
            result = predicate()
            if result:
                return result
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise RuntimeError(label)


def run():
    require(not os.getenv("WEBSITE_INSTANCE_ID"), "LOCAL_FIXTURE_REQUIRED")
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "fixture": "synthetic Hub, YouTube and schedules; actual loopback API and independent worker",
        "browser_used": False,
        "external_network_used": False,
        "source_sha256": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in SOURCES},
    }
    with tempfile.TemporaryDirectory(prefix="anke-websub-proof-") as temporary:
        folder = Path(temporary)
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "ANKE_SPORTS_ENV": "local",
            "ANKE_SPORTS_STORAGE_BACKEND": "documents-local",
            "ANKE_SPORTS_DOCUMENT_LOCAL_PATH": str(folder / "documents.db"),
            "ANKE_SPORTS_LOCAL_PREVIEW": "true",
            "ANKE_SPORTS_PUBLIC_URL": PUBLIC,
            "ANKE_SPORTS_WEB_URL": base,
            "ANKE_SPORTS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "ANKE_SPORTS_FIREBASE_PROJECT_ID": "",
            "ANKE_SPORTS_YOUTUBE_PROJECT_ID": "synthetic-websub-proof",
            "ANKE_SPORTS_YOUTUBE_DAILY_BUDGET": "200",
            "ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED": "true",
            "ANKE_SPORTS_ENABLED_SPORTS_PROVIDERS": "[]",
            "YOUTUBE_API_KEY": "synthetic-no-network",
            "ANKE_WEBSUB_FIXTURE_DIR": temporary,
            "ANKE_WEBSUB_FIXTURE_BASE": base,
        }
        stamp = datetime.now(timezone.utc)
        start = stamp + timedelta(days=1)
        teams = [
            {"id": "proof:LAL", "name": "Lakers", "short_name": "LAL", "color": "#fff"},
            {"id": "proof:GSW", "name": "Warriors", "short_name": "GSW", "color": "#fff"},
        ]
        source = {
            "id": "proof:league",
            "name": "Synthetic league",
            "short_name": "DEMO",
            "sport": "basketball",
            "kind": "competition",
            "color": "#16844a",
            "demo": True,
        }
        event = {
            "id": "websub-proof-event",
            "source_key": "proof:event",
            "competition_id": source["id"],
            "sport": "basketball",
            "title": "Lakers vs Warriors (synthetic)",
            "starts_at": start.isoformat(),
            "local_date": start.date().isoformat(),
            "time_precision": "exact",
            "timezone": "UTC",
            "duration": 120,
            "venue": "Synthetic venue",
            "status": "scheduled",
            "participants": teams,
            "provider": "websub-proof",
            "source_url": "",
            "updated_at": stamp.isoformat(),
            "demo": True,
        }
        fixture = {
            "events": [event],
            "sources": [source],
            "published_at": stamp.isoformat(),
            "title": f"Lakers Warriors {start.date().isoformat()} preview",
        }
        write_json(folder / "fixture.json", fixture)
        worker = api = None
        processes = []
        with (
            (folder / "process.log").open("wb") as log,
            httpx.Client(base_url=base, headers={"Origin": base}, timeout=10) as client,
        ):

            def launch(mode):
                process = subprocess.Popen(
                    [sys.executable, "-m", "experiments.document_websub_verify", "--" + mode],
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                processes.append(process)
                return process

            def counts():
                return (folder / "trace").read_text().splitlines() if (folder / "trace").exists() else []

            try:
                api = launch("api")
                wait_for(lambda: client.get("/api/v1/health").status_code == 200, "API_START_TIMEOUT", [api])
                worker = launch("worker")
                require(client.post("/api/v1/auth/local").status_code == 200, "LOCAL_LOGIN")
                profile = client.get("/api/v1/me/calendar").json()
                follows = {
                    "expected_revision": profile["revision"],
                    "follows": [{"type": "competition", "source_key": source["id"]}],
                }
                preview = client.post("/api/v1/me/follows/preview", json=follows)
                require(preview.status_code == 200, "FOLLOW_PREVIEW")
                follows["confirmation"] = preview.json()["confirmation"]
                profile = client.put("/api/v1/me/follows", json=follows).json()
                require(
                    client.post(
                        "/api/v1/me/creators", json={"url": CHANNEL, "expected_revision": profile["revision"]}
                    ).status_code
                    == 200,
                    "SAVE_CREATOR",
                )
                private = urlsplit(client.get("/api/v1/me/feed/address").json()["url"])
                require(
                    private.hostname == "websub-fixture.example" and private.path.startswith("/feeds/"),
                    "ISOLATED_FEED_REQUIRED",
                )

                def published(contains):
                    profile = client.get("/api/v1/me/calendar").json()
                    response = client.get(private.path)
                    return (
                        response
                        if (
                            profile["feed"]["status"] == "published"
                            and profile["creators"][0]["sync_status"] == "current"
                            and response.status_code == 200
                            and (VIDEO in response.text) == contains
                        )
                        else None
                    )

                before = wait_for(lambda: published(True), "INITIAL_CONTENT_TIMEOUT", [api, worker])
                wait_for(lambda: counts().count("hub:subscribe") == 1, "HUB_SUBSCRIBE_TIMEOUT", [api, worker])
                require(
                    client.get("/api/v1/me/calendar").json()["creators"][0]["websub_status"] == "verified",
                    "LEASE_NOT_VERIFIED",
                )
                report["checks"].append("persisted_intent_and_actual_http_verification")
                original_hub = json.loads((folder / "hub.json").read_text())

                def notify(version, title="Untrusted hint", forged=False):
                    hub = json.loads((folder / "hub.json").read_text())
                    payload = (
                        f'<feed xmlns="http://www.w3.org/2005/Atom" '
                        f'xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>{VIDEO}</yt:videoId>'
                        f"<yt:channelId>{CHANNEL}</yt:channelId><updated>{version}</updated>"
                        f"<title>{title}</title></entry></feed>"
                    ).encode()
                    signed = "sha1=" + (
                        "0" * 40
                        if forged
                        else hmac.new(hub["hub.secret"].encode(), payload, hashlib.sha1).hexdigest()
                    )
                    return client.post(
                        urlsplit(hub["hub.callback"]).path,
                        content=payload,
                        headers={"X-Hub-Signature": signed},
                    )

                fixture["title"] = "Unrelated trade news (synthetic)"
                write_json(folder / "fixture.json", fixture)
                require(notify("2026-09-10T00:00:00Z", forged=True).status_code == 204, "FORGED_ACK")
                require(notify("2026-09-10T00:00:00Z").status_code == 204, "SIGNED_NOTIFY")
                after = wait_for(lambda: published(False), "NOTIFICATION_PUBLICATION_TIMEOUT", [api, worker])
                first_event = Calendar.from_ical(before.content).walk("VEVENT")[0]
                next_event = Calendar.from_ical(after.content).walk("VEVENT")[0]
                require(str(first_event["UID"]) == str(next_event["UID"]), "UID_CHANGED")
                require(int(next_event["SEQUENCE"]) == int(first_event["SEQUENCE"]) + 1, "SEQUENCE_MISMATCH")
                report["checks"].append("signed_notification_to_api_metadata_to_same_ics_event")
                report["sequence"] = [int(first_event["SEQUENCE"]), int(next_event["SEQUENCE"])]
                reads = counts().count("youtube:videos")
                require(
                    notify("2026-09-10T00:00:00Z", title="Duplicate with altered hint").status_code == 204,
                    "DUPLICATE_ACK",
                )
                stop(worker)
                require(worker.poll() is not None, "WORKER_NOT_STOPPED")
                worker = launch("worker")
                wait_for(lambda: counts().count("worker:boot") == 2, "WORKER_RESTART_TIMEOUT", [api, worker])
                time.sleep(2.5)
                require(
                    worker.poll() is None
                    and counts().count("youtube:videos") == reads
                    and counts().count("hub:subscribe") == 1,
                    "RESTART_OR_DUPLICATE_REPEATED_NETWORK",
                )
                require(client.get(private.path).content == after.content, "DUPLICATE_CHANGED_ICS")
                require(
                    client.get(private.path, headers={"If-None-Match": after.headers["etag"]}).status_code
                    == 304
                    and client.head(private.path).status_code == 200,
                    "CONDITIONAL_FEED_FAILED",
                )
                report["checks"].append("restart_preserves_verified_lease_and_semantic_receipt")
                profile = client.get("/api/v1/me/calendar").json()
                require(
                    client.patch(
                        f"/api/v1/me/creators/{CHANNEL}",
                        json={
                            "enabled": False,
                            "scope_keys": [],
                            "preview": True,
                            "recap": True,
                            "expected_revision": profile["revision"],
                        },
                    ).status_code
                    == 200,
                    "PAUSE_CREATOR",
                )
                wait_for(lambda: counts().count("hub:unsubscribe") == 1, "UNSUBSCRIBE_TIMEOUT", [api, worker])
                final_hub = json.loads((folder / "hub.json").read_text())
                require(
                    final_hub["hub.callback"] == original_hub["hub.callback"]
                    and final_hub["hub.secret"] == original_hub["hub.secret"],
                    "CALLBACK_IDENTITY_CHANGED",
                )
                require(notify("2026-09-10T00:00:01Z").status_code == 204, "PAUSED_ACK")
                require(
                    client.get("/api/v1/me/calendar").json()["creators"][0]["websub_status"]
                    == "unsubscribed",
                    "UNSUBSCRIBE_NOT_VERIFIED",
                )
                report["checks"].append("pause_stops_notifications_and_verifies_unsubscribe")
                require(client.post("/api/v1/auth/logout").status_code == 200, "LOGOUT")
                report["youtube_calls"] = {
                    name: counts().count("youtube:" + name)
                    for name in ["channels", "playlistItems", "videos"]
                }
                report["hub_calls"] = {
                    name: counts().count("hub:" + name) for name in ["subscribe", "unsubscribe"]
                }
                report["worker_restarts"] = 1
                report["feed_sha256"] = hashlib.sha256(after.content).hexdigest()
            finally:
                for process in reversed(processes):
                    stop(process)
                report["processes_stopped"] = all(p.poll() is not None for p in processes)
    report["temporary_data_removed"] = not folder.exists()
    require(
        all(
            hashlib.sha256(Path(p).read_bytes()).hexdigest() == v for p, v in report["source_sha256"].items()
        ),
        "SOURCE_CHANGED_DURING_PROOF",
    )
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.api or args.worker:
        raise SystemExit(child("api" if args.api else "worker"))
    require(args.output and not args.output.exists(), "NEW_OUTPUT_PATH_REQUIRED")
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, result)
    print(
        json.dumps(
            {"success": True, "checks": result["checks"], "processes_stopped": result["processes_stopped"]}
        )
    )
