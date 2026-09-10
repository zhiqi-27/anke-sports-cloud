"""Exercise a fresh synthetic creator preview through loopback HTTP and its separate worker."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx
from icalendar import Calendar


def require(ok, label):
    if not ok:
        raise ValueError(label)


def run(base):
    parsed = urlsplit(base)
    require(
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
        and parsed.port
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"},
        "LOOPBACK_REQUIRED",
    )
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "fixture": "synthetic YouTube and schedules",
        "browser_used": False,
        "external_requests": False,
    }
    files = [
        "app/document_channels.py",
        "app/document_creators.py",
        "app/document_matches.py",
        "app/document_runtime.py",
        "app/document_accounts.py",
        "app/document_api.py",
        "app/document_worker.py",
        "app/youtube_content_rules.py",
        "experiments/document_creators_ui.py",
        "experiments/document_ui.py",
        "experiments/document_creators_verify.py",
    ]
    report["source_sha256"] = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in files}
    with httpx.Client(base_url=base, headers={"Origin": base}, timeout=20) as client:
        marker = client.get("/api/v1/local/creator-fixture")
        require(
            marker.status_code == 200 and marker.json().get("fixture") == "document-creators-v1",
            "CREATOR_FIXTURE_REQUIRED",
        )
        fixture = marker.json()
        require(client.get("/creators").status_code == 200, "DESKTOP_HTML_AVAILABLE")
        report["checks"].append("desktop_html_available")
        user = client.post("/api/v1/auth/local")
        require(user.status_code == 200, "LOCAL_LOGIN")
        require(not user.json()["config"]["creators"], "FRESH_CREATOR_FIXTURE_REQUIRED")
        try:
            follows = {
                "expected_revision": user.json()["revision"],
                "follows": [{"type": "competition", "source_key": "document-demo:league"}],
            }
            preview = client.post("/api/v1/me/follows/preview", json=follows)
            require(preview.status_code == 200, "FOLLOW_PREVIEW")
            follows["confirmation"] = preview.json()["confirmation"]
            saved = client.put("/api/v1/me/follows", json=follows)
            require(saved.status_code == 200, "FOLLOW_SAVE")
            data = {"url": fixture["channel_id"], "expected_revision": saved.json()["revision"]}
            first = client.post(
                "/api/v1/me/creators", json=data, headers={"Idempotency-Key": "creator-http-proof"}
            )
            require(first.status_code == 200, "CREATOR_SAVE")
            replay = client.post(
                "/api/v1/me/creators", json=data, headers={"Idempotency-Key": "creator-http-proof"}
            )
            require(replay.status_code == 200 and replay.json() == first.json(), "EXACT_CREATOR_REPLAY")
            report["checks"].append("creator_save_and_exact_replay")
            address = client.get("/api/v1/me/feed/address")
            require(address.status_code == 200, "PRIVATE_ADDRESS")
            feed_url = address.json()["url"]
            require(feed_url.startswith(base + "/feeds/"), "PRIVATE_FEED_LOCAL")

            def snapshot():
                profile = client.get("/api/v1/me/calendar").json()
                event = client.get("/api/v1/events/document-demo-03").json()
                reviews = client.get("/api/v1/me/reviews").json()
                response = client.get(feed_url)
                return profile, event, reviews, response

            def wait_for(predicate, label):
                until = time.monotonic() + 45
                while time.monotonic() < until:
                    result = snapshot()
                    if predicate(*result):
                        return result
                    time.sleep(0.25)
                raise ValueError(label)

            profile, event, reviews, response = wait_for(
                lambda p, e, r, f: (
                    p["feed"]["status"] == "published"
                    and p["creators"][0]["sync_status"] == "current"
                    and f.status_code == 200
                    and fixture["automatic_video"] in f.text
                    and fixture["recap_video"] in f.text
                    and bool(r["items"])
                ),
                "BACKGROUND_CONTENT_TIMEOUT",
            )
            ics = {str(row["UID"]): row for row in Calendar.from_ical(response.content).walk("VEVENT")}
            require(len(ics) == 12, "TWELVE_UNIQUE_EVENTS")
            report["checks"].append("background_preview_recap_and_reviews")
            report["event_count"] = len(ics)
            report["initial_review_count"] = len(reviews["items"])
            item = next(row for row in reviews["items"] if row["event_id"] == "document-demo-03")
            confirmed = client.post(
                "/api/v1/me/reviews/" + item["id"],
                json={"decision": "confirm", "kind": "preview", "expected_updated_at": item["updated_at"]},
            )
            require(confirmed.status_code == 200, "REVIEW_CONFIRM")
            _, event, _, confirmed_feed = wait_for(
                lambda p, e, r, f: (
                    p["feed"]["status"] == "published"
                    and f.status_code == 200
                    and fixture["review_video"] in f.text
                ),
                "CONFIRM_PUBLICATION_TIMEOUT",
            )
            auto = next(link for link in event["links"] if fixture["automatic_video"] in link["url"])
            before = {
                str(row["UID"]): row for row in Calendar.from_ical(confirmed_feed.content).walk("VEVENT")
            }
            uid = next(
                uid for uid, row in before.items() if fixture["automatic_video"] in str(row["DESCRIPTION"])
            )
            blocked = client.post("/api/v1/me/links/" + auto["id"] + "/block")
            require(blocked.status_code == 200, "BLOCK_AUTOMATIC_LINK")
            _, _, _, blocked_feed = wait_for(
                lambda p, e, r, f: (
                    p["feed"]["status"] == "published"
                    and f.status_code == 200
                    and fixture["automatic_video"] not in f.text
                ),
                "BLOCK_PUBLICATION_TIMEOUT",
            )
            after = {str(row["UID"]): row for row in Calendar.from_ical(blocked_feed.content).walk("VEVENT")}
            require(set(before) == set(after), "UIDS_RETAINED")
            require(
                int(after[uid]["SEQUENCE"]) == int(before[uid]["SEQUENCE"]) + 1,
                "SAME_EVENT_SEQUENCE_ADVANCES",
            )
            require(fixture["review_video"] in str(after[uid]["DESCRIPTION"]), "CONFIRMED_LINK_RETAINED")
            report["checks"].append("confirm_then_block_updates_same_event")
            report["sequence_before_block"], report["sequence_after_block"] = (
                int(before[uid]["SEQUENCE"]),
                int(after[uid]["SEQUENCE"]),
            )
            require(
                client.post(f"/api/v1/me/creators/{fixture['channel_id']}/refresh").status_code == 200,
                "REFRESH_REQUEST",
            )
            # A forced refresh must finish, rather than accepting the prior 'current' response.
            previous_sync = profile["creators"][0]["last_synced_at"]
            _, _, _, final_feed = wait_for(
                lambda p, e, r, f: (
                    p["feed"]["status"] == "published"
                    and p["creators"][0]["sync_status"] == "current"
                    and p["creators"][0]["last_synced_at"] > previous_sync
                    and f.status_code == 200
                    and fixture["automatic_video"] not in f.text
                ),
                "REDISCOVERY_TIMEOUT",
            )
            require(final_feed.content == blocked_feed.content, "REDISCOVERY_PRESERVES_BLOCK_AND_FEED")
            require(
                client.get(feed_url, headers={"If-None-Match": final_feed.headers["etag"]}).status_code
                == 304,
                "FEED_304",
            )
            require(client.head(feed_url).status_code == 200, "FEED_HEAD")
            report["checks"].append("rediscovery_preserves_block_and_conditional_feed")
            report["final_feed_sha256"] = hashlib.sha256(final_feed.content).hexdigest()
            report["uid_digest"] = hashlib.sha256("\n".join(sorted(after)).encode()).hexdigest()
        finally:
            require(client.post("/api/v1/auth/logout").status_code == 200, "LOCAL_SESSION_LOGOUT")
            report["checks"].append("local_session_logged_out")
    require(
        all(
            hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected
            for name, expected in report["source_sha256"].items()
        ),
        "SOURCE_CHANGED_DURING_PROBE",
    )
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:3008")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "NEW_OUTPUT_REQUIRED")
    try:
        report = run(args.url.rstrip("/"))
    except Exception as exc:
        print(
            json.dumps(
                {"success": False, "error": str(exc) if type(exc) is ValueError else type(exc).__name__}
            )
        )
        return 1
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"success": True, "checks": report["checks"], "event_count": report["event_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
