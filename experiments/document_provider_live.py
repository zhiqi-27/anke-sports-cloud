"""Read the real Jolpica schedule via isolated HTTP + persistent document worker.

No main database, cloud credentials, Azure resources or browser interaction.
The temporary processes, local account, encryption key and documents are cleaned.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from cryptography.fernet import Fernet
import httpx
from icalendar import Calendar


def require(ok, label):
    if not ok:
        raise ValueError(label)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run():
    require(
        not os.getenv("WEBSITE_INSTANCE_ID") and os.getenv("ANKE_SPORTS_ENV", "local") == "local",
        "LOCAL_ONLY",
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "environment": "documents-local",
        "upstream": "jolpica",
        "browser_used": False,
    }
    root = Path(__file__).resolve().parents[1]
    source_paths = [
        "app/config.py",
        "app/document_api.py",
        "app/document_accounts.py",
        "app/document_catalog.py",
        "app/document_providers.py",
        "app/document_runtime.py",
        "app/document_worker.py",
        "app/document_store.py",
        "app/provider_adapters.py",
        "app/job_rules.py",
        "experiments/document_provider_live.py",
    ]
    report["source_sha256"] = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in source_paths
    }
    with tempfile.TemporaryDirectory(prefix="anke-document-provider-") as directory:
        env = {
            **os.environ,
            "ANKE_SPORTS_ENV": "local",
            "ANKE_SPORTS_STORAGE_BACKEND": "documents-local",
            "ANKE_SPORTS_DOCUMENT_LOCAL_PATH": str(Path(directory) / "documents.db"),
            "ANKE_SPORTS_LOCAL_PREVIEW": "true",
            "ANKE_SPORTS_WEB_URL": base,
            "ANKE_SPORTS_PUBLIC_URL": base,
            "ANKE_SPORTS_ENABLED_SPORTS_PROVIDERS": "[]",
            "ANKE_SPORTS_FIREBASE_PROJECT_ID": "",
            "ANKE_SPORTS_FIREBASE_CREDENTIALS_JSON": "",
            "ANKE_SPORTS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "BALLDONTLIE_API_KEY": "",
            "FOOTBALL_DATA_API_KEY": "",
            "YOUTUBE_API_KEY": "",
        }
        with open(Path(directory) / "process.log", "wb") as log:
            api = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                env=env,
                stdout=log,
                stderr=log,
            )
            worker = None
            try:
                with httpx.Client(base_url=base, headers={"Origin": base}, timeout=15) as client:

                    def get(path):
                        response = client.get(path)
                        require(response.status_code == 200, "LOCAL_HTTP_READ_FAILED")
                        return response.json()

                    def post(path, data=None):
                        response = client.post(path, json=data)
                        require(response.status_code == 200, "LOCAL_HTTP_COMMAND_FAILED")
                        return response.json()

                    def wait_for(check, label, timeout=100):
                        deadline = time.monotonic() + timeout
                        while time.monotonic() < deadline:
                            require(
                                api.poll() is None and (worker is None or worker.poll() is None),
                                "PROCESS_EXITED",
                            )
                            try:
                                value = check()
                                if value:
                                    return value
                            except httpx.ConnectError:
                                pass
                            time.sleep(0.5)
                        raise ValueError(label)

                    wait_for(
                        lambda: get("/api/v1/health")["storage_backend"] == "documents-local",
                        "API_STARTUP_TIMEOUT",
                        15,
                    )
                    post("/api/v1/auth/local")
                    post("/api/v1/local/providers/jolpica/sync")
                    post("/api/v1/local/providers/jolpica/sync")
                    worker = subprocess.Popen(
                        [sys.executable, "-m", "app.document_worker"], env=env, stdout=log, stderr=log
                    )

                    def synced():
                        statuses = get("/api/v1/status")["providers"]
                        row = next((r for r in statuses if r["id"] == "jolpica"), None)
                        if row and row["error"]:
                            raise ValueError("LIVE_PROVIDER_FAILED_" + row["error"])
                        return row if row and row["last_success"] and row["activity"] == "idle" else None

                    provider = wait_for(synced, "PROVIDER_SYNC_TIMEOUT")
                    report["provider"] = provider
                    report["checks"].append("real_provider_completed_via_http_and_worker")
                    sources = get("/api/v1/sources?dataset=real")["items"]
                    require(
                        any(row["id"] == "jolpica:f1" and not row["demo"] for row in sources),
                        "F1_SOURCE_MISSING",
                    )
                    user = get("/api/v1/me/calendar")
                    data = {
                        "expected_revision": user["revision"],
                        "follows": [{"type": "competition", "source_key": "jolpica:f1"}],
                    }
                    preview = post("/api/v1/me/follows/preview", data)
                    response = client.put(
                        "/api/v1/me/follows", json={**data, "confirmation": preview["confirmation"]}
                    )
                    require(response.status_code == 200, "FOLLOW_FAILED")

                    def published():
                        user = get("/api/v1/me/calendar")
                        return (
                            user
                            if user["feed"]["status"] == "published" and user["feed"]["event_count"] > 0
                            else None
                        )

                    user = wait_for(published, "FEED_PUBLICATION_TIMEOUT")
                    address = get("/api/v1/me/feed/address")["url"]
                    response = client.get(address)
                    require(response.status_code == 200, "FEED_READ_FAILED")
                    events = Calendar.from_ical(response.content).walk("VEVENT")
                    uids = [str(row["UID"]) for row in events]
                    require(
                        len(events) == user["feed"]["event_count"] and len(set(uids)) == len(events),
                        "FEED_IDENTITY_COUNT_MISMATCH",
                    )
                    report.update(
                        event_count=len(events),
                        feed_revision=user["feed"]["revision"],
                        uid_digest=hashlib.sha256(json.dumps(sorted(uids)).encode()).hexdigest(),
                        body_sha256=hashlib.sha256(response.content).hexdigest(),
                    )
                    require(
                        client.get(address, headers={"If-None-Match": response.headers["etag"]}).status_code
                        == 304,
                        "ETAG_FAILED",
                    )
                    require(client.head(address).status_code == 200, "HEAD_FAILED")
                    report["checks"] += [
                        "personal_follow_and_published_ics",
                        "unique_uids",
                        "conditional_304",
                        "head_200",
                    ]
                    query = {
                        "from": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
                        "to": (datetime.now(timezone.utc) + timedelta(days=120)).isoformat(),
                        "dataset": "real",
                        "limit": 200,
                    }
                    calendar = client.get("/api/v1/events", params=query)
                    require(
                        calendar.status_code == 200 and bool(calendar.json()["items"]), "CALENDAR_READ_FAILED"
                    )
                    require(
                        all(
                            row["provider"] == "jolpica" and not row["demo"] and row["included"]
                            for row in calendar.json()["items"]
                        ),
                        "CALENDAR_PROVENANCE_FAILED",
                    )
                    report["calendar_rows"] = len(calendar.json()["items"])
                    report["checks"].append("real_calendar_provenance_and_inclusion")
                    stop(worker)
                    worker = subprocess.Popen(
                        [sys.executable, "-m", "app.document_worker", "--once"],
                        env=env,
                        stdout=log,
                        stderr=log,
                    )
                    require(worker.wait(timeout=30) == 0, "RESTARTED_WORKER_CYCLE_FAILED")
                    again = synced()
                    require(
                        again and again["last_success"] == provider["last_success"], "RESTART_FETCHED_EARLY"
                    )
                    require(client.get(address).content == response.content, "RESTART_CHANGED_FEED")
                    report["checks"].append("worker_restart_preserves_deadline_and_published_feed")
                    post("/api/v1/auth/logout")
                    require(client.get("/api/v1/me/calendar").status_code == 401, "LOGOUT_FAILED")
                    report["checks"].append("local_session_signed_out")
            finally:
                if worker:
                    stop(worker)
                stop(api)
                report["processes_stopped"] = api.poll() is not None and (
                    worker is None or worker.poll() is not None
                )
    require(
        all(
            hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
            for name, expected in report["source_sha256"].items()
        ),
        "SOURCE_CHANGED_DURING_PROBE",
    )
    report["temporary_directory_removed"] = not Path(directory).exists()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/document-provider-live.json")
    args = parser.parse_args()
    try:
        result = run()
    except Exception as error:
        from app.job_rules import error_code

        print(json.dumps({"passed": False, "error": error_code(error)}))
        return 1
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": True,
                "checks": len(result["checks"]),
                "event_count": result["event_count"],
                "cleaned": result["temporary_directory_removed"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
