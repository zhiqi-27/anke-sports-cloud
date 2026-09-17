import argparse
import time
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.calendar import rebuild_feed
from app.db import ProviderState, SessionLocal, User, now
from app.jobs import PROVIDERS, LeaseLost, claim_job, complete_job, error_code, fail_job
from app.providers import enqueue_provider, provider_due, sync_provider
from app.service import enqueue


def execute_claim(db, claim):
    kind, payload = claim.kind, claim.payload
    if kind == "identity_cleanup":
        from app.privacy import cleanup_identity

        cleanup_identity(db, payload)
        return
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
    instant = now()
    with SessionLocal() as db:
        identifiers = db.scalars(
            select(ProviderState.id)
            .where(ProviderState.id.in_(PROVIDERS), *provider_due(instant))
            .order_by(ProviderState.id)
        ).all()
    queued = 0
    for ident in identifiers:
        with SessionLocal() as db:
            queued += enqueue_provider(db, ident, scheduled=True, instant=instant)
            db.commit()
    return queued


def run_maintenance():
    from app.oauth import clean_expired_connections
    from app.broadcasts import schedule_broadcasts

    from app.public_feeds import schedule_public_feeds

    healthy = True
    for operation in (
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
    next_schedule = 0
    next_maintenance = 0
    while True:
        healthy = True
        if time.monotonic() >= next_maintenance:
            next_maintenance = time.monotonic() + 60
            healthy = run_maintenance()
        if time.monotonic() >= next_schedule:
            try:
                schedule_providers()
                next_schedule = time.monotonic() + 60
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
