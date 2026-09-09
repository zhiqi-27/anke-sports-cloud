import argparse
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.calendar import rebuild_feed
from app.db import Job, ProviderState, SessionLocal, User, now
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
            else:
                raise ValueError("UNKNOWN_JOB")
            job = db.get(Job, ident)
            job.state, job.error = "done", ""
            db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(Job, ident)
            job.error = str(exc) if isinstance(exc, ValueError) and str(exc).isupper() else type(exc).__name__
            job.state = "failed" if job.attempts >= 5 or job.error.endswith("KEY_REQUIRED") else "pending"
            job.due_at = (
                datetime.now(timezone.utc) + timedelta(seconds=min(600, 2**job.attempts * 10))
            ).isoformat()
            if kind == "provider":
                state = db.get(ProviderState, payload["provider"])
                if state:
                    state.error = job.error
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
    while True:
        if time.monotonic() >= next_schedule:
            schedule_providers()
            next_schedule = time.monotonic() + 6 * 3600
        completed = run_one()
        if args.once:
            break
        if not completed:
            time.sleep(2)
