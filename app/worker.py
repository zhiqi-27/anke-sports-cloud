import argparse
import time
import logging

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.calendar import rebuild_feed
from app.db import Job, ProviderState, SessionLocal, User, Video, now
from app.jobs import LeaseLost, claim_job, complete_job, error_code, fail_job
from app.providers import sync_provider
from app.service import enqueue


def execute_claim(db, claim):
    kind, payload = claim.kind, claim.payload
    if payload.get("user_id") and kind != "projection":
        user = db.get(User, payload["user_id"])
        if not user or user.deleted:
            return
    if kind == "projection":
        rebuild_feed(db, payload["user_id"])
    elif kind == "public_projection":
        from app.public_feeds import rebuild_public_feed

        rebuild_public_feed(db, payload["feed_id"])
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
            request_subscription(payload["channel_id"], claim)
        else:
            raise ValueError("UNKNOWN_JOB")
    else:
        raise ValueError("UNKNOWN_JOB")


def run_one(job_id: str | None = None) -> bool:
    try:
        with SessionLocal() as db:
            claim, progressed = claim_job(db, job_id)
            db.commit()
    except (LeaseLost, IntegrityError):
        # A competing claim/first provider-row insert won. The caller can poll other work.
        return False
    if not claim:
        return progressed
    try:
        with SessionLocal() as db:
            execute_claim(db, claim)
            complete_job(db, claim)
            db.commit()
    except LeaseLost:
        # The context closed with rollback, including all handler changes and follow-up jobs.
        pass
    except Exception as exc:
        try:
            with SessionLocal() as db:
                fail_job(db, claim, exc)
                db.commit()
        except LeaseLost:
            pass
    return True


def schedule_providers():
    with SessionLocal() as db:
        for provider in db.scalars(
            select(ProviderState).where(
                ProviderState.enabled.is_(True),
                or_(ProviderState.next_attempt_at.is_(None), ProviderState.next_attempt_at <= now()),
            )
        ):
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


def run_maintenance():
    from app.websub import schedule_content
    from app.oauth import clean_expired_connections
    from app.broadcasts import schedule_broadcasts

    from app.public_feeds import schedule_public_feeds

    healthy = True
    for operation in (
        schedule_content,
        clean_expired_connections,
        schedule_broadcasts,
        schedule_public_feeds,
    ):
        try:
            operation()
        except Exception as exc:
            logging.warning("MAINTENANCE_FAILED operation=%s code=%s", operation.__name__, error_code(exc))
            healthy = False
    return healthy


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    next_schedule = time.monotonic() + 6 * 3600
    next_content = 0
    while True:
        healthy = True
        if time.monotonic() >= next_content:
            next_content = time.monotonic() + 60
            healthy = run_maintenance()
        if time.monotonic() >= next_schedule:
            try:
                schedule_providers()
                next_schedule = time.monotonic() + 6 * 3600
            except Exception as exc:
                logging.warning("PROVIDER_SCHEDULER_FAILED code=%s", error_code(exc))
                next_schedule = time.monotonic() + 60
                healthy = False
        try:
            completed = run_one()
        except Exception as exc:
            logging.warning("WORKER_CYCLE_FAILED code=%s", error_code(exc))
            completed, healthy = False, False
        if args.once:
            return 0 if healthy else 1
        if not completed:
            time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
