"""Run the packaged app under real Core Tools and Azurite, with disposable local data."""

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

from scripts.package_functions import build_package


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(check, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (ConnectionError, OSError):
            pass
        time.sleep(0.5)
    raise RuntimeError("FUNCTIONS_EXPERIMENT_DEADLINE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    root = Path(__file__).resolve().parents[1]
    func, azurite = shutil.which("func"), shutil.which("azurite")
    if not func or not azurite or os.getenv("WEBSITE_INSTANCE_ID"):
        raise RuntimeError("LOCAL_CORE_TOOLS_AND_AZURITE_REQUIRED")
    from cryptography.fernet import Fernet
    import httpx
    from azure.storage.queue import QueueClient

    processes = []
    stage = "emulator_start"
    queue_error = None
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="anke-functions-") as folder:
        work = Path(folder)
        package = build_package(root, work / "source.zip")
        app_root = work / "app"
        app_root.mkdir()
        with zipfile.ZipFile(work / "source.zip") as archive:
            archive.extractall(app_root)
        # Every child gets a clean environment: no Azure, Firebase or provider credentials.
        env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ}
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        blob_port, queue_port, table_port, http_port = (free_port() for _ in range(4))
        account = "ankefunctiontest"
        key = base64.b64encode(os.urandom(64)).decode()
        connection = (
            f"DefaultEndpointsProtocol=http;AccountName={account};AccountKey={key};"
            f"BlobEndpoint=http://127.0.0.1:{blob_port}/{account};"
            f"QueueEndpoint=http://127.0.0.1:{queue_port}/{account};"
            f"TableEndpoint=http://127.0.0.1:{table_port}/{account};"
        )
        values = {
            "FUNCTIONS_WORKER_RUNTIME": "python",
            "AzureWebJobsStorage": connection,
            "AzureQueueConnection": connection,
            "ANKE_SPORTS_ENV": "local",
            "ANKE_SPORTS_LOCAL_PREVIEW": "false",
            "ANKE_SPORTS_DATABASE_URL": "sqlite:///" + str(work / "fixture.db"),
            "ANKE_SPORTS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "ANKE_SPORTS_FIREBASE_PROJECT_ID": "",
            "ANKE_SPORTS_PUBLIC_URL": f"http://127.0.0.1:{http_port}",
            "ANKE_SPORTS_WEB_URL": "http://127.0.0.1:3000",
            "ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED": "false",
            "ANKE_SPORTS_BROADCAST_CHECKS_ENABLED": "false",
            "BALLDONTLIE_API_KEY": "", "FOOTBALL_DATA_API_KEY": "", "YOUTUBE_API_KEY": "",
        }
        settings_file = app_root / "local.settings.json"
        settings_file.write_text(json.dumps({"IsEncrypted": False, "Values": values,
                                           "Host": {"LocalHttpPort": http_port}}))
        settings_file.chmod(0o600)
        env.update(values)
        env["AZURITE_ACCOUNTS"] = f"{account}:{key}"
        # Test logs stay in the temporary directory and are never emitted as raw output.
        log_path = work / "runtime.log"
        with log_path.open("w+") as log:
            log_path.chmod(0o600)
            try:
                processes.append(subprocess.Popen([
                    azurite, "--silent", "--location", str(work / "storage"),
                    "--blobHost", "127.0.0.1", "--blobPort", str(blob_port),
                    "--queueHost", "127.0.0.1", "--queuePort", str(queue_port),
                    "--tableHost", "127.0.0.1", "--tablePort", str(table_port),
                ], env=env, cwd=work, stdout=log, stderr=log, start_new_session=True))
                queue = QueueClient.from_connection_string(
                    connection, "anke-sports-jobs", api_version="2025-11-05",
                    connection_timeout=2, read_timeout=2, retry_total=0,
                )
                def queue_ready():
                    nonlocal queue_error
                    try:
                        queue.create_queue()
                        return True
                    except Exception as exc:
                        queue_error = {"type": type(exc).__name__,
                                       "code": str(getattr(exc, "error_code", "")),
                                       "status": getattr(exc, "status_code", None)}
                        if type(exc).__name__ == "ResourceExistsError":
                            return True
                        return False
                wait_until(queue_ready, 30)
                stage = "host_start"
                print(json.dumps({"progress": "Azurite ready; starting Functions host"}), flush=True)
                # Create a single explicitly synthetic event and outbox item before host startup.
                os.chdir(app_root)
                os.environ.update(values)
                from datetime import datetime, timedelta, timezone
                from sqlalchemy import select
                from app.db import Base, Event, Feed, Job, OAuthRequest, SessionLocal, engine
                from app.service import ensure_user, save_config
                from app.config import settings
                from icalendar import Calendar

                Base.metadata.create_all(engine)
                with SessionLocal() as db:
                    owner = ensure_user(db, "functions-fixture")
                    start = datetime.now(timezone.utc) + timedelta(days=1)
                    event = Event(source_key="fixture:functions", competition_id="fixture:league",
                                  sport="basketball", title="【合成Functions验收】测试比赛",
                                  starts_at=start.isoformat(), local_date=start.date().isoformat(),
                                  participants=[], provider="FIXTURE", demo=True)
                    db.add(event)
                    save_config(db, owner, {**owner.config, "event_overrides": [
                        {"event_key": event.source_key, "state": "include"}]}, owner.revision)
                    db.commit()
                    job_id = db.scalar(select(Job.id))
                    feed = db.scalar(select(Feed))
                    token = settings().cipher().decrypt(feed.token_ciphertext.encode()).decode()
                processes.append(subprocess.Popen([func, "start", "--port", str(http_port)],
                    env=env, cwd=app_root, stdout=log, stderr=log, start_new_session=True))
                base = f"http://127.0.0.1:{http_port}"
                client = httpx.Client(base_url=base, timeout=10, trust_env=False)
                def http_ready():
                    try:
                        return client.get("/api/v1/status").status_code == 200
                    except httpx.HTTPError:
                        return False
                wait_until(http_ready, 180)
                stage = "timer_queue_feed"
                print(json.dumps({"progress": "Functions HTTP ready; waiting for real minute timer"}), flush=True)
                assert client.get("/api/v1/me/calendar").status_code == 401
                assert client.post("/api/v1/auth/local", headers={"Origin": values["ANKE_SPORTS_WEB_URL"]}).status_code == 404
                mcp_headers = {"Accept": "application/json, text/event-stream",
                               "MCP-Protocol-Version": "2025-11-25"}
                discovery = client.post("/mcp/public", headers=mcp_headers,
                                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                discovery.raise_for_status()
                public_tools = sorted(item["name"] for item in discovery.json()["result"]["tools"])
                assert public_tools == ["get_event", "get_schedule", "search_sources"]
                lookup = client.post("/mcp/public", headers=mcp_headers,
                                     json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                           "params": {"name": "get_event", "arguments": {"event_id": event.id}}})
                lookup.raise_for_status()
                assert not lookup.json()["result"].get("isError")
                assert "合成Functions验收" in json.dumps(lookup.json(), ensure_ascii=False)
                # No direct call to dispatch_outbox/process_job/run_one: the real minute timer dispatches.
                def published():
                    with SessionLocal() as db:
                        return db.get(Job, job_id).state == "done"
                wait_until(published, 95)
                first = client.get(f"/feeds/{token}.ics")
                first.raise_for_status()
                uids = [str(item["UID"]) for item in Calendar.from_ical(first.content).walk("VEVENT")]
                assert len(uids) == 1
                assert client.get(f"/feeds/{token}.ics", headers={"If-None-Match": first.headers["etag"]}).status_code == 304
                queue.send_message(json.dumps({"job_id": job_id}))
                queue.send_message(json.dumps({"job_id": job_id}))
                wait_until(lambda: queue.get_queue_properties().approximate_message_count == 0, 45)
                again = client.get(f"/feeds/{token}.ics")
                assert first.content == again.content and first.headers["etag"] == again.headers["etag"]
                with SessionLocal() as db:
                    assert db.get(Job, job_id).attempts == 1
                # Invoke maintenance through the host's local admin endpoint, not a Python direct call.
                with SessionLocal() as db:
                    db.add(OAuthRequest(id="expired-functions-fixture", client_id="fixture-client",
                                        params={}, expires_at=int(time.time()) - 60, code_expires_at=0))
                    db.commit()
                maintenance = client.post("/admin/functions/update_content", json={"input": None})
                assert maintenance.status_code == 202
                def cleaned():
                    with SessionLocal() as db:
                        return db.get(OAuthRequest, "expired-functions-fixture") is None
                wait_until(cleaned, 20)
                log.flush()
                raw_log = log_path.read_text()
                assert token not in raw_log and key not in raw_log
                result = {"scope": "local Core Tools + Azurite + SQLite, synthetic event",
                          "core_tools": subprocess.check_output([func, "--version"], text=True).strip(),
                          "azurite": subprocess.check_output([azurite, "--version"], text=True).strip(),
                          "package_sha256": package["sha256"], "package_files": len(package["files"]),
                          "http_status": 200, "anonymous_personal": 401, "local_identity": 404,
                          "real_minute_timer_to_queue_to_feed": True, "event_count": len(uids),
                          "conditional_feed": 304, "duplicate_messages_unchanged": True,
                          "job_attempts": 1, "maintenance_host_accepted": maintenance.status_code,
                          "expired_request_removed": True, "public_mcp_tools": public_tools,
                          "public_mcp_get_event": True, "local_logs_no_feed_token_or_storage_key": True,
                          "not_verified": ["Azure cloud resources/network/TLS", "Firebase through Functions",
                                           "device calendar refresh", "poison queue and cloud telemetry"]}
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, indent=2) + "\n")
                print(json.dumps(result), flush=True)
                client.close()
                engine.dispose()
            except Exception as exc:
                # Sanitized diagnostics only; never private feed URLs, local settings or SQL parameters.
                log.flush()
                raw = log_path.read_text()
                markers = [line for line in raw.splitlines() if any(word in line for word in (
                    "ModuleNotFoundError", "No job functions found", "Worker failed", "Error indexing",
                    "Unable to load", "Exception:", "Reason:"))]
                for secret in (key, values["ANKE_SPORTS_ENCRYPTION_KEY"], locals().get("token", "")):
                    if secret:
                        markers = [line.replace(secret, "[redacted]") for line in markers]
                print(json.dumps({"failed": type(exc).__name__, "stage": stage,
                                  "queue_error": queue_error, "diagnostics": markers[-8:]}), flush=True)
                raise RuntimeError("FUNCTIONS_EXPERIMENT_FAILED") from None
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5)
                os.chdir(original_cwd)


if __name__ == "__main__":
    main()
