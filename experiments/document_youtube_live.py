"""Two public channel reads; retain this experiment's quota ledger across runs.

No browser, Firebase login, creator follow, user database, Azure or background job.
This local ledger does not claim to count older experiments or Google Console usage.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import SecretStr

from experiments.firebase_lifecycle import CheckFailed, exclusive_json, private_json, require

PROJECT = "anke-sports-dev"
CHANNEL = "UCB_qr75-ydFVKSF9Dmo6izg"


def run(key_path, report):
    require(not os.getenv("WEBSITE_INSTANCE_ID"), "LOCAL_ONLY")
    key = private_json(key_path)
    require(key.get("project_id") == PROJECT, "YOUTUBE_PROJECT_MISMATCH")
    require(
        key.get("key_resource") == "projects/736683203171/locations/global/keys/anke-sports-youtube-dev",
        "YOUTUBE_KEY_MISMATCH",
    )
    require(isinstance(key.get("api_key"), str) and bool(key["api_key"]), "YOUTUBE_KEY_REQUIRED")
    # Remove process-key precedence; the explicitly inspected private file is the only credential.
    os.environ.pop("YOUTUBE_API_KEY", None)
    from app.config import Settings
    from app.document_runtime import Runtime
    from app.document_store import LocalDocumentStore

    cfg = Settings(
        _env_file=None,
        env="local",
        storage_backend="documents-local",
        document_local_path="data/document-youtube-live.db",
        local_preview=False,
        encryption_key=Fernet.generate_key().decode(),
        youtube_project_id=PROJECT,
        youtube_daily_budget=4,
        YOUTUBE_API_KEY=SecretStr(key["api_key"]),
    )
    store = LocalDocumentStore(cfg.document_local_path)
    try:
        rt = Runtime(store, cfg)
        before = rt.youtube_budget.status()
        report["budget_before"] = before
        require(before["state"] == "available" and before["available_units"] >= 2, "EXPERIMENT_BUDGET_WAIT")
        for value in [CHANNEL, "@Formula1"]:
            details = rt.resolve_creator(value)
            require(details["channel_id"] == CHANNEL and bool(details["name"]), "CHANNEL_IDENTITY_MATCH")
            report["checks"].append("channel_id_resolved" if value == CHANNEL else "handle_resolved")
        report["channel"] = {"id": details["channel_id"], "name": details["name"]}
        after = rt.youtube_budget.status()
        require(after["reserved_units"] == before["reserved_units"] + 2, "TWO_RESERVATIONS_RETAINED")
        report["budget_after"] = after
        report["checks"].append("two_reservations_retained")
        reopened = LocalDocumentStore(cfg.document_local_path)
        try:
            require(Runtime(reopened, cfg).youtube_budget.status() == after, "REOPENED_LEDGER_MATCHES")
        finally:
            reopened.close()
        report["checks"].append("reopened_ledger_matches")
        require(not store.changes("state")[0], "NO_BUSINESS_STATE_WRITTEN")
        report["checks"].append("no_business_state_written")
        import sys

        require("app.db" not in sys.modules and "app.sql_app" not in sys.modules, "SQL_RUNTIME_ABSENT")
        report["checks"].append("sql_runtime_absent")
        report["ledger_retained"] = cfg.document_local_path
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "NEW_OUTPUT_REQUIRED")
    logging.disable(logging.CRITICAL)
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "success": False,
        "environment": "documents-local",
        "upstream": "YouTube Data API",
        "key_transport": "X-Goog-Api-Key header",
        "browser_used": False,
        "cloud_resources_changed": False,
        "creator_discovery_verified": False,
        "quota_scope": "This experiment ledger only; previous callers and Google usage are not counted",
    }
    paths = [
        "app/document_youtube_budget.py",
        "app/youtube_transport.py",
        "app/youtube_rules.py",
        "app/document_runtime.py",
        "app/document_store.py",
        "app/provider_adapters.py",
        "experiments/document_youtube_live.py",
    ]
    report["source_sha256"] = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in paths}
    try:
        run(args.key_file, report)
        require(
            all(
                hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected
                for name, expected in report["source_sha256"].items()
            ),
            "SOURCE_CHANGED_DURING_PROBE",
        )
        report["success"] = True
    except Exception as exc:
        report["failure"] = str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    exclusive_json(args.output, report)
    print(
        json.dumps(
            {
                "success": report["success"],
                "checks": report["checks"],
                "failure": report.get("failure"),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
