import argparse
import time
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from fastapi import HTTPException

from app.calendar import rebuild_feed
from app.db import ChannelSync, Creator, Job, ProviderState, SessionLocal, User, Video, now
from app.providers import sync_provider
from app.service import enqueue


def run_one(job_id: str | None = None) -> bool:
    with SessionLocal() as db:
        query = (
            select(Job)
            .where(Job.state.in_(["pending", "running"]), Job.due_at <= now())
            .order_by(Job.created_at)
            .limit(1)
        )
        if job_id:
            query = query.where(Job.id == job_id)
        job = db.scalar(query)
        if not job:
            return False
        lease = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        claimed = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.due_at == job.due_at, Job.state == job.state)
            .values(state="running", due_at=lease, attempts=job.attempts + 1)
        )
        if not claimed.rowcount:
            return False
        ident, kind, payload = job.id, job.kind, job.payload
        db.commit()
    try:
        with SessionLocal() as db:
            if kind == "projection":
                rebuild_feed(db, payload["user_id"])
            elif kind == "provider":
                sync_provider(db, payload["provider"])
                for user in db.scalars(select(User).where(User.deleted.is_(False))):
                    enqueue(db, "projection", {"user_id": user.id})
            elif kind == "broadcast_check":
                from app.broadcasts import check_record

                check_record(db, payload["link_id"], payload["url_hash"])
            elif kind.startswith("youtube_"):
                from app.content import match_video, poll_channel, refresh_channel_metadata, refresh_videos
                from app.websub import request_subscription

                if kind == "youtube_poll":
                    poll_channel(db, payload)
                elif kind == "youtube_videos":
                    refresh_videos(db, payload["channel_id"], payload["video_ids"])
                elif kind == "youtube_channel_metadata":
                    refresh_channel_metadata(db, payload["channel_id"])
                elif kind == "youtube_rematch":
                    for video in db.scalars(select(Video).where(Video.channel_id == payload["channel_id"])):
                        match_video(db, video, only_user=payload["user_id"])
                elif kind == "youtube_subscribe":
                    request_subscription(payload["channel_id"])
                else:
                    raise ValueError("UNKNOWN_JOB")
            else:
                raise ValueError("UNKNOWN_JOB")
            job = db.get(Job, ident)
            job.state, job.error = "done", ""
            db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(Job, ident)
            candidate = (
                exc.detail.get("code", "")
                if isinstance(exc, HTTPException) and isinstance(exc.detail, dict)
                else str(exc)
                if isinstance(exc, ValueError)
                else ""
            )
            job.error = candidate if re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", candidate) else type(exc).__name__
            job.state = "failed" if job.attempts >= 5 or job.error.endswith("KEY_REQUIRED") else "pending"
            job.due_at = (
                datetime.now(timezone.utc) + timedelta(seconds=min(600, 2**job.attempts * 10))
            ).isoformat()
            if kind == "provider":
                state = db.get(ProviderState, payload["provider"])
                if state:
                    state.error = job.error
            if kind.startswith("youtube_") and payload.get("channel_id"):
                sync = db.get(ChannelSync, payload["channel_id"])
                creator = db.get(Creator, payload["channel_id"])
                if sync:
                    sync.error = job.error
                    sync.next_poll_at = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
                if creator:
                    creator.last_error = job.error
            db.commit()
    return True


def schedule_providers():
    with SessionLocal() as db:
        for provider in db.scalars(select(ProviderState).where(ProviderState.enabled.is_(True))):
            pending = db.scalar(
                select(Job.id).where(
                    Job.kind == "provider",
                    Job.state.in_(["pending", "running"]),
                    Job.payload["provider"].as_string() == provider.id,
                )
            )
            if not pending:
                enqueue(db, "provider", {"provider": provider.id})
        db.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    next_schedule = time.monotonic() + 6 * 3600
    next_content = 0
    while True:
        if time.monotonic() >= next_content:
            from app.websub import schedule_content
            from app.oauth import clean_expired_connections

            schedule_content()
            clean_expired_connections()
            from app.broadcasts import schedule_broadcasts

            schedule_broadcasts()
            next_content = time.monotonic() + 60
        if time.monotonic() >= next_schedule:
            schedule_providers()
            next_schedule = time.monotonic() + 6 * 3600
        completed = run_one()
        if args.once:
            break
        if not completed:
            time.sleep(2)
