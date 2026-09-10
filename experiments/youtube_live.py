"""Bounded real YouTube HTTP/worker probe with an isolated local test owner.

Copies only cached public Jolpica rows from a read-only source database. Never
changes an existing user's follows or injects real videos into the main preview.
Uses the real provider, budget and worker handlers; no provider monkeypatches.
"""

import argparse
from collections import Counter
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

import httpx
from icalendar import Calendar
from pydantic import SecretStr

from experiments.codex_business import fixture
from experiments.firebase_lifecycle import CheckFailed, exclusive_json, private_json, require

PROJECT = "anke-sports-dev"
CHANNEL = "UCB_qr75-ydFVKSF9Dmo6izg"


def run(key, source, report):
    require(key.get("project_id") == PROJECT and key.get("key_resource") ==
            "projects/736683203171/locations/global/keys/anke-sports-youtube-dev", "youtube_project_mismatch")
    require(isinstance(key.get("api_key"), str) and bool(key["api_key"]), "youtube_key_required")
    require(source.is_file(), "public_schedule_source_required")
    report["checks"] = []

    def passed(label, ok=True):
        require(ok, label)
        report["checks"].append(label)
        print(json.dumps({"check": label, "passed": True}), flush=True)

    with fixture() as (base, owner, _):
        from sqlalchemy import func, select
        from app.config import settings
        from app.db import ChannelSync, Creator, Event, Job, Link, SessionLocal, Source, Video, VideoMatch, YouTubeBudget

        settings().youtube_project_id = PROJECT
        settings().youtube_api_key = SecretStr(key["api_key"])
        settings().youtube_daily_budget = 20
        os.environ.update({"ANKE_SPORTS_YOUTUBE_PROJECT_ID": PROJECT, "YOUTUBE_API_KEY": key["api_key"],
                           "ANKE_SPORTS_YOUTUBE_DAILY_BUDGET": "20"})
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original, SessionLocal() as db:
            original.row_factory = sqlite3.Row
            rows = [dict(row) for row in original.execute("SELECT * FROM events WHERE provider='jolpica' AND demo=0")]
            sources = [dict(row) for row in original.execute("SELECT * FROM sources WHERE provider='jolpica' AND demo=0")]
            require(0 < len(rows) <= 200 and len(sources) == 1, "unexpected_public_schedule_scope")
            report["public_schedule"] = {"provider": "jolpica", "cached_events": len(rows),
                "copied_rows_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                "fresh_upstream_fetch": False}
            for row in sources:
                db.add(Source(**row))
            for row in rows:
                row["participants"] = json.loads(row["participants"])
                db.add(Event(**row))
            db.commit()
        revision = owner.get("/api/v1/me/calendar").json()["revision"]
        follow = owner.put("/api/v1/me/follows", json={"expected_revision": revision,
            "follows": [{"type": "competition", "source_key": "jolpica:f1"}]})
        passed("isolated_owner_follows_cached_real_f1", follow.status_code == 200)

        def drain():
            # Fresh process, same temporary DB/project budget. No timer/provider scheduling.
            command = ("from app.worker import run_one\n"
                       "for _ in range(40):\n"
                       "    if not run_one(): break\n"
                       "else: raise SystemExit(2)\n")
            result = subprocess.run([sys.executable, "-c", command], capture_output=True, timeout=55)
            require(result.returncode == 0, "worker_process_failed")
            with SessionLocal() as db:
                unfinished = db.scalars(select(Job).where(Job.state != "done")).all()
                report["unfinished_jobs"] = [{"kind": j.kind, "state": j.state, "error": j.error}
                                             for j in unfinished]
                require(not unfinished, "worker_jobs_not_completed")

        def usage():
            with SessionLocal() as db:
                row = db.get(YouTubeBudget, PROJECT)
                return row.reserved_units if row else 0

        drain()
        address = owner.get("/api/v1/me/feed/address").json()["url"]
        require(address.startswith(base + "/"), "private_feed_not_local")
        with httpx.Client(timeout=20, trust_env=False) as reader:
            first = reader.get(address)
            initial = {str(e["UID"]): int(e["SEQUENCE"]) for e in Calendar.from_ical(first.content).walk("VEVENT")}
            passed("baseline_real_f1_http_feed", first.status_code == 200 and len(initial) > 0)
            resolved = owner.post("/api/v1/me/creators/resolve", json={"url": "@Formula1"})
            passed("http_resolves_real_youtube_channel", resolved.status_code == 200
                   and resolved.json()["channel_id"] == CHANNEL)
            body = {"url": "@Formula1", "scope_keys": ["jolpica:f1"], "preview": True, "recap": True,
                    "expected_revision": follow.json()["revision"]}
            headers = {"Idempotency-Key": "youtube-live-add-creator"}
            added = owner.post("/api/v1/me/creators", json=body, headers=headers)
            passed("http_adds_real_creator", added.status_code == 200)
            before_retry = usage()
            repeated = owner.post("/api/v1/me/creators", json=body, headers=headers)
            passed("idempotent_add_has_no_extra_google_reservation", repeated.status_code == 200
                   and repeated.json() == added.json() and usage() == before_retry)
            drain()
            with SessionLocal() as db:
                sync, creator = db.get(ChannelSync, CHANNEL), db.get(Creator, CHANNEL)
                videos = db.scalars(select(Video).where(Video.channel_id == CHANNEL)).all()
                matched = db.scalar(select(func.count()).select_from(VideoMatch))
                links = db.scalar(select(func.count()).select_from(Link).where(Link.channel_id == CHANNEL))
                matches = db.scalars(select(VideoMatch)).all()
                report["matching_observation"] = {
                    "decisions": dict(Counter(m.decision for m in matches)),
                    "reason_counts": dict(Counter(reason for m in matches for reason in m.reason_codes)),
                    "samples": [
                        {"video_title": title, "event_title": event_title, "kind": kind,
                         "decision": decision, "reason_codes": reasons}
                        for title, event_title, kind, decision, reasons in db.execute(
                            select(Video.title, Event.title, VideoMatch.kind, VideoMatch.decision,
                                   VideoMatch.reason_codes)
                            .join(VideoMatch, VideoMatch.video_id == Video.id)
                            .join(Event, Event.id == VideoMatch.event_id)
                            .where(Video.title.ilike("%race%"))
                            .order_by(Video.published_at.desc(), VideoMatch.event_id).limit(6)
                        )
                    ],
                    "human_labels": 0,
                }
                report["youtube"] = {"channel_id": CHANNEL, "channel_name": creator.name,
                    "videos_stored": len(videos), "available_videos": sum(v.available for v in videos),
                    "match_rows": matched, "personal_links": links, "last_success": sync.last_success,
                    "next_poll_at": sync.next_poll_at,
                    "completed_job_kinds": sorted(set(db.scalars(select(Job.kind))))}
                passed("worker_stores_real_public_video_metadata", len(videos) > 0 and all(v.available for v in videos))
                passed("uploads_and_channel_metadata_completed", bool(sync.last_success)
                       and not sync.error and not creator.last_error)
                budget = db.get(YouTubeBudget, PROJECT)
                report["budget"] = {"project_id": budget.project_id, "period": budget.period,
                    "reserved_units": budget.reserved_units, "daily_limit": budget.daily_limit,
                    "google_remaining_balance_verified": False}
                passed("api_and_fresh_worker_share_project_budget", 3 < budget.reserved_units <= 20)
            final = reader.get(address)
            final_events = Calendar.from_ical(final.content).walk("VEVENT")
            versions = {str(e["UID"]): int(e["SEQUENCE"]) for e in final_events}
            passed("discovery_preserves_original_feed_uids", initial.keys() == versions.keys()
                   and all(versions[key] >= version for key, version in initial.items()))
            descriptions = [str(e.get("DESCRIPTION", "")) for e in final_events]
            report["feed"] = {"events": len(versions),
                "events_with_added_content": sum(versions[key] > version for key, version in initial.items()),
                "events_with_youtube_url": sum("youtube.com/watch?v=" in d for d in descriptions)}
            report["real_video_to_ics_verified"] = links > 0 and report["feed"]["events_with_youtube_url"] > 0
            before = usage()
            drain()
            stable = reader.get(address, headers={"If-None-Match": final.headers["etag"]})
            passed("idle_worker_no_google_calls_and_feed_304", stable.status_code == 304 and usage() == before)
            passed("real_creator_visible_in_calendar_api", owner.get("/api/v1/me/calendar").status_code == 200)
    report["temporary_api_and_db_removed"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--public-schedule-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "new_output_required")
    logging.disable(logging.CRITICAL)
    report = {"date": datetime.now(timezone.utc).isoformat(), "success": False,
        "identity": "explicit isolated local fixture", "provider": "real YouTube Data API",
        "websub_tested": False, "matching_accuracy_verified": False, "device_calendar_tested": False,
        "prior_cloud_shell_calls_outside_this_ledger": 3}
    try:
        run(private_json(args.key_file), args.public_schedule_db, report)
        report["success"] = True
    except Exception as error:
        report["failure"] = str(error) if isinstance(error, CheckFailed) else type(error).__name__
    report["source_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in [
        Path(__file__).relative_to(Path.cwd()), Path("experiments/codex_business.py"),
        Path("app/providers.py"), Path("app/content.py"), Path("app/worker.py"), Path("app/youtube_budget.py"),
    ]}
    exclusive_json(args.output, report)
    print(json.dumps({"success": report["success"], "checks_passed": len(report.get("checks", [])),
                      "failure": report.get("failure"), "budget": report.get("budget")}), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
