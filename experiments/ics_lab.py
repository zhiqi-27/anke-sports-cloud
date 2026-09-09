"""Stable-URL iCalendar lab. Synthetic events only; no credentials or live schedules."""

import argparse
import hashlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.calendar import serialize
from app.db import Projection

ROOT = Path("data/ics-lab")
PHASES = ["baseline", "description", "reschedule", "cancel", "past-recap"]


def generate():
    ROOT.mkdir(parents=True, exist_ok=True)
    if (ROOT / "0.ics").exists():
        raise SystemExit("Existing lab retained. Reuse its files to preserve subscribed identities.")
    baseline = datetime.now(timezone.utc).replace(microsecond=0)
    projections = []
    for ident, title, days in [
        ("description", "描述变更", 2),
        ("reschedule", "比赛改期", 3),
        ("cancel", "比赛取消", 4),
        ("past-recap", "过去事件追加复盘", -2),
    ]:
        start = baseline + timedelta(days=days)
        projections.append(
            Projection(
                id=f"anke-ics-lab-{ident}",
                event_id=ident,
                feed_id="lab",
                version=0,
                updated_at=baseline.isoformat(),
                removed=False,
                data={
                    "title": f"[Anke Sports 实验] {title}",
                    "starts_at": start.isoformat(),
                    "local_date": start.date().isoformat(),
                    "time_precision": "exact",
                    "status": "scheduled",
                    "duration": 90,
                    "venue": "合成实验，无真实比赛",
                    "description": "初始内容。仅用于日历刷新验收。",
                    "transparent": True,
                    "url": "",
                },
            )
        )
    for phase, label in enumerate(PHASES):
        if phase:
            p = projections[phase - 1]
            p.version += 1
            p.updated_at = (baseline + timedelta(minutes=phase)).isoformat()
            p.data = dict(p.data)
            if label == "description":
                p.data["description"] = (
                    "描述已变更：新增前瞻链接测试。https://example.com/anke-sports-lab\n这是占位实验链接，不是真实视频。"
                )
            elif label == "reschedule":
                p.data["starts_at"] = (
                    datetime.fromisoformat(p.data["starts_at"]) + timedelta(hours=2)
                ).isoformat()
            elif label == "cancel":
                p.data["status"] = "cancelled"
            elif label == "past-recap":
                p.data["description"] = "过去事件新增复盘。仅验证描述更新，不包含真实比赛内容。"
        (ROOT / f"{phase}.ics").write_bytes(serialize(projections))
    publish(0)


def publish(phase):
    source = ROOT / f"{phase}.ics"
    if not source.exists():
        raise SystemExit("Run init before publish.")
    temporary = ROOT / "current.tmp"
    temporary.write_bytes(source.read_bytes())
    temporary.replace(ROOT / "current.ics")
    print(f"Published phase {phase}: {PHASES[phase]}; bytes={source.stat().st_size}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path != "/calendar.ics" or not (ROOT / "current.ics").exists():
            self.send_error(404)
            return
        body = (ROOT / "current.ics").read_bytes()
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        status = 304 if self.headers.get("If-None-Match") == etag else 200
        self.send_response(status)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.end_headers()
        if status == 200:
            self.wfile.write(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "publish", "serve"])
    parser.add_argument("--phase", type=int, choices=range(5), default=0)
    args = parser.parse_args()
    if args.command == "init":
        generate()
    elif args.command == "publish":
        publish(args.phase)
    else:
        print("Synthetic ICS lab on loopback port 8890; path /calendar.ics; no access logs.")
        HTTPServer(("127.0.0.1", 8890), Handler).serve_forever()
