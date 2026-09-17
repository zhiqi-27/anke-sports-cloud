"""Durable claims, fenced completion and operator recovery; no transport credentials."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update

from app.db import Job, JobReplay, ProviderState, now
from app.job_rules import error_code as error_code

MAX_ATTEMPTS = 5
LEASE_SECONDS = 300
PROVIDERS = {"jolpica", "balldontlie", "football-data"}
ACTIVE_JOB_KINDS = {"identity_cleanup", "projection", "public_projection", "provider", "broadcast_check"}


class LeaseLost(Exception):
    """The result must roll back because another execution owns this work."""


@dataclass(frozen=True)
class Claim:
    id: str
    kind: str
    payload: dict
    attempt: int
    lease: str

    @property
    def provider(self):
        value = self.payload.get("provider") if self.kind == "provider" else None
        return value if value in PROVIDERS else None

def owned(claim):
    return (
        Job.id == claim.id,
        Job.state == "running",
        Job.attempts == claim.attempt,
        Job.due_at == claim.lease,
    )


def provider_owned(claim):
    return (
        ProviderState.id == claim.provider,
        ProviderState.lease_job_id == claim.id,
        ProviderState.lease_attempt == claim.attempt,
        ProviderState.lease_until == claim.lease,
    )


def claim_job(db, job_id=None):
    """Return (claim, progressed). A deferred/exhausted job is progress without execution."""
    instant = now()
    query = (
        select(Job)
        .where(
            Job.kind.in_(ACTIVE_JOB_KINDS),
            Job.state.in_(["pending", "running"]),
            Job.due_at <= instant,
        )
        .order_by(Job.due_at, Job.created_at, Job.id)
        .limit(1)
    )
    if job_id:
        query = query.where(Job.id == job_id)
    job = db.scalar(query)
    if not job:
        return None, False
    expected = (
        Job.id == job.id,
        Job.state == job.state,
        Job.due_at == job.due_at,
        Job.attempts == job.attempts,
    )
    if job.attempts - job.quota_waits >= MAX_ATTEMPTS:
        changed = db.execute(
            update(Job)
            .where(*expected)
            .values(state="failed", error=job.error or "WORKER_LEASE_EXPIRED", finished_at=instant)
        )
        return None, bool(changed.rowcount)
    claim = Claim(
        job.id,
        job.kind,
        dict(job.payload),
        job.attempts + 1,
        (datetime.fromisoformat(instant) + timedelta(seconds=LEASE_SECONDS)).isoformat(),
    )
    if claim.provider:
        state = db.get(ProviderState, claim.provider)
        if not state:
            state = ProviderState(id=claim.provider)
            db.add(state)
            db.flush()
        # Recheck a busy provider soon after its current fetch can finish; cooldowns
        # remain authoritative and do not consume this job's attempt budget.
        busy_retry = (datetime.fromisoformat(instant) + timedelta(seconds=15)).isoformat()
        deadline = max(state.next_attempt_at or instant, min(state.lease_until or instant, busy_retry))
        if deadline > instant:
            changed = db.execute(update(Job).where(*expected).values(state="pending", due_at=deadline))
            return None, bool(changed.rowcount)
    if claim.provider:
        locked = db.execute(
            update(ProviderState)
            .where(
                ProviderState.id == claim.provider,
                or_(ProviderState.lease_until.is_(None), ProviderState.lease_until <= instant),
                or_(ProviderState.next_attempt_at.is_(None), ProviderState.next_attempt_at <= instant),
            )
            .values(
                lease_job_id=claim.id,
                lease_attempt=claim.attempt,
                lease_until=claim.lease,
                last_attempt_at=instant,
            )
        )
        if not locked.rowcount:
            raise LeaseLost()
    changed = db.execute(
        update(Job).where(*expected).values(state="running", due_at=claim.lease, attempts=claim.attempt)
    )
    if not changed.rowcount:
        raise LeaseLost()
    return claim, True


def complete_job(db, claim):
    # Handler changes, follow-up outbox work and both fences share this transaction.
    if claim.provider:
        locked = db.execute(
            update(ProviderState)
            .where(*provider_owned(claim))
            .values(
                consecutive_failures=0,
                next_attempt_at=None,
                lease_job_id=None,
                lease_attempt=None,
                lease_until=None,
                error="",
            )
        )
        if not locked.rowcount:
            raise LeaseLost()
    changed = db.execute(update(Job).where(*owned(claim)).values(state="done", error="", finished_at=now()))
    if not changed.rowcount:
        raise LeaseLost()


def retry_seconds(exc, attempt, instant):
    from app.job_rules import retry_seconds as shared_retry_seconds

    return shared_retry_seconds(exc, attempt, instant)


def fail_job(db, claim, exc):
    instant = datetime.fromisoformat(now())
    code = error_code(exc)
    job = db.get(Job, claim.id)
    if not job:
        raise LeaseLost()
    effective_attempt = claim.attempt
    terminal = (
        effective_attempt >= MAX_ATTEMPTS or code.endswith(("KEY_REQUIRED", "PROJECT_REQUIRED"))
    )
    seconds = retry_seconds(exc, effective_attempt, instant)
    if claim.provider:
        state = db.get(ProviderState, claim.provider)
        if not state:
            raise LeaseLost()
        failures = state.consecutive_failures + 1
        if failures >= 3 or code.endswith("KEY_REQUIRED"):
            seconds = max(
                seconds,
                6 * 3600 if code.endswith("KEY_REQUIRED") else min(6 * 3600, 900 * 2 ** min(failures - 3, 5)),
            )
        deadline = (instant + timedelta(seconds=seconds)).isoformat()
        locked = db.execute(
            update(ProviderState)
            .where(*provider_owned(claim))
            .values(
                consecutive_failures=failures,
                next_attempt_at=deadline,
                error=code,
                lease_job_id=None,
                lease_attempt=None,
                lease_until=None,
            )
        )
        if not locked.rowcount:
            raise LeaseLost()
    deadline = (instant + timedelta(seconds=seconds)).isoformat()
    changed = db.execute(
        update(Job)
        .where(*owned(claim))
        .values(
            state="failed" if terminal else "pending",
            quota_waits=job.quota_waits,
            error=code,
            due_at=deadline,
            finished_at=instant.isoformat() if terminal else None,
        )
    )
    if not changed.rowcount:
        raise LeaseLost()


def replay_job(db, job_id, expected_attempts, reason, apply=False):
    """One auditable replay per failed job. Replaying its successor requires a new explicit action."""
    original = db.get(Job, job_id)
    if not original or original.state != "failed" or original.attempts != expected_attempts:
        raise ValueError("FAILED_JOB_CHANGED")
    if original.kind not in ACTIVE_JOB_KINDS:
        raise ValueError("JOB_KIND_RETIRED")
    if not 3 <= len(reason.strip()) <= 500:
        raise ValueError("REPLAY_REASON_REQUIRED")
    owner_id = original.payload.get("user_id")
    if owner_id:
        from app.service import lock_user

        owner = lock_user(db, owner_id)
        if not owner or owner.deleted != (original.kind == "identity_cleanup"):
            raise ValueError("OWNER_UNAVAILABLE")
    previous = db.get(JobReplay, job_id)
    if previous:
        return {"source_id": job_id, "new_job_id": previous.new_job_id, "created": False}
    if not apply:
        return {
            "source_id": job_id,
            "kind": original.kind,
            "attempts": original.attempts,
            "created": False,
            "dry_run": True,
        }
    # Lock/check the source before creating its successor; the unique source ID also fences concurrent operators.
    guarded = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.state == "failed", Job.attempts == expected_attempts)
        .values(state="failed")
    )
    if not guarded.rowcount:
        raise ValueError("FAILED_JOB_CHANGED")
    successor = Job(kind=original.kind, payload=dict(original.payload))
    db.add(successor)
    db.flush()
    db.add(JobReplay(source_id=job_id, new_job_id=successor.id, reason=reason.strip()))
    db.flush()
    return {"source_id": job_id, "new_job_id": successor.id, "created": True}
