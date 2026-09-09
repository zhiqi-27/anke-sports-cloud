"""Durable claims, fenced completion and operator recovery; no transport credentials."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import re

import httpx
from fastapi import HTTPException
from sqlalchemy import or_, select, update

from app.db import ChannelSync, ChannelWork, Creator, Job, JobReplay, ProviderState, now
from app.youtube_budget import NETWORK_JOBS, WAIT_CODES

MAX_ATTEMPTS = 5
LEASE_SECONDS = 300
PROVIDERS = {"jolpica", "balldontlie", "football-data"}
CHANNEL_KINDS = {
    "youtube_poll",
    "youtube_videos",
    "youtube_channel_metadata",
    "youtube_rematch",
    "youtube_subscribe",
}


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

    @property
    def channel(self):
        return self.payload.get("channel_id") if self.kind in CHANNEL_KINDS else None

    @property
    def channel_resource(self):
        if not self.channel:
            return None
        return self.channel + (":hub" if self.kind == "youtube_subscribe" else ":data")

    @property
    def channel_network(self):
        return bool(self.channel and self.kind != "youtube_rematch")


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


def channel_owned(claim):
    return (
        ChannelWork.id == claim.channel_resource,
        ChannelWork.lease_job_id == claim.id,
        ChannelWork.lease_attempt == claim.attempt,
        ChannelWork.lease_until == claim.lease,
    )


def guard_channel_claim(db, claim):
    """Fence a separately committed Hub intent before it can change or send anything."""
    if not claim.channel_resource or claim.lease <= now():
        raise LeaseLost()
    # A write lock on the channel, followed by the job, matches completion lock order.
    channel = db.execute(update(ChannelWork).where(*channel_owned(claim)).values(lease_until=claim.lease))
    job = db.execute(update(Job).where(*owned(claim)).values(due_at=claim.lease))
    if not channel.rowcount or not job.rowcount:
        raise LeaseLost()


def claim_job(db, job_id=None):
    """Return (claim, progressed). A deferred/exhausted job is progress without execution."""
    instant = now()
    query = (
        select(Job)
        .where(Job.state.in_(["pending", "running"]), Job.due_at <= instant)
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
        expired = Claim(job.id, job.kind, dict(job.payload), job.attempts, job.due_at)
        if (
            job.state == "running"
            and expired.channel_resource
            and db.scalar(select(ChannelWork.id).where(*channel_owned(expired)))
        ):
            fail_job(db, expired, ValueError("WORKER_LEASE_EXPIRED"))
            return None, True
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
    if claim.kind in NETWORK_JOBS:
        from app.youtube_budget import status as budget_status

        budget = budget_status(db)
        if budget["resume_at"] and budget["resume_at"] > instant:
            changed = db.execute(
                update(Job).where(*expected).values(state="pending", due_at=budget["resume_at"])
            )
            return None, bool(changed.rowcount)
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
    if claim.channel_resource:
        state = db.get(ChannelWork, claim.channel_resource)
        if not state:
            state = ChannelWork(id=claim.channel_resource)
            db.add(state)
            db.flush()
        busy_retry = (datetime.fromisoformat(instant) + timedelta(seconds=15)).isoformat()
        cooldown = state.next_attempt_at if claim.channel_network else None
        deadline = max(cooldown or instant, min(state.lease_until or instant, busy_retry))
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
    if claim.channel_resource:
        predicates = [
            ChannelWork.id == claim.channel_resource,
            or_(ChannelWork.lease_until.is_(None), ChannelWork.lease_until <= instant),
        ]
        if claim.channel_network:
            predicates.append(
                or_(ChannelWork.next_attempt_at.is_(None), ChannelWork.next_attempt_at <= instant)
            )
        locked = db.execute(
            update(ChannelWork)
            .where(*predicates)
            .values(lease_job_id=claim.id, lease_attempt=claim.attempt, lease_until=claim.lease)
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
    if claim.channel_resource:
        values = dict(lease_job_id=None, lease_attempt=None, lease_until=None)
        if claim.channel_network:
            values.update(consecutive_failures=0, next_attempt_at=None, error="")
        locked = db.execute(update(ChannelWork).where(*channel_owned(claim)).values(**values))
        if not locked.rowcount:
            raise LeaseLost()
    changed = db.execute(update(Job).where(*owned(claim)).values(state="done", error="", finished_at=now()))
    if not changed.rowcount:
        raise LeaseLost()
    if claim.channel_network and claim.kind != "youtube_subscribe":
        sync, creator = db.get(ChannelSync, claim.channel), db.get(Creator, claim.channel)
        if sync and sync.error != "HUB_DENIED":
            sync.error = ""
        if creator:
            creator.last_error = ""


def error_code(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        return "UPSTREAM_RATE_LIMITED" if exc.response.status_code == 429 else "UPSTREAM_HTTP_ERROR"
    if isinstance(exc, httpx.RequestError):
        return "UPSTREAM_NETWORK_ERROR"
    candidate = (
        exc.detail.get("code", "")
        if isinstance(exc, HTTPException) and isinstance(exc.detail, dict)
        else str(exc)
        if isinstance(exc, ValueError)
        else ""
    )
    return (
        candidate
        if isinstance(candidate, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", candidate)
        else type(exc).__name__
    )


def retry_seconds(exc, attempt, instant):
    seconds = min(600, 2**attempt * 10)
    if (
        isinstance(exc, HTTPException)
        and isinstance(exc.detail, dict)
        and exc.detail.get("code") in WAIT_CODES
    ):
        delay = exc.detail.get("retry_after_seconds")
        if isinstance(delay, (int, float)) and delay > 0:
            seconds = max(seconds, min(delay, 30 * 86400))
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {403, 429, 503}:
        value = exc.response.headers.get("Retry-After", "")
        try:
            requested = (
                float(value) if value.isdigit() else (parsedate_to_datetime(value) - instant).total_seconds()
            )
            # Provider delays up to 30 days are retained; invalid values use backoff.
            if requested > 0:
                seconds = max(seconds, min(requested, 30 * 86400))
        except (ValueError, TypeError, OverflowError):
            pass
    return seconds


def fail_job(db, claim, exc):
    instant = datetime.fromisoformat(now())
    code = error_code(exc)
    job = db.get(Job, claim.id)
    if not job:
        raise LeaseLost()
    waiting = code in WAIT_CODES
    effective_attempt = claim.attempt - job.quota_waits
    terminal = not waiting and (
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
    if claim.channel_resource:
        state = db.get(ChannelWork, claim.channel_resource)
        if not state:
            raise LeaseLost()
        values = dict(lease_job_id=None, lease_attempt=None, lease_until=None)
        if claim.channel_network:
            failures = state.consecutive_failures + (0 if waiting else 1)
            if not waiting and (failures >= 3 or code.endswith(("KEY_REQUIRED", "PROJECT_REQUIRED"))):
                seconds = max(
                    seconds,
                    6 * 3600
                    if code.endswith("KEY_REQUIRED")
                    else min(6 * 3600, 900 * 2 ** min(failures - 3, 5)),
                )
            values.update(
                consecutive_failures=failures,
                error=code,
                next_attempt_at=(instant + timedelta(seconds=seconds)).isoformat(),
            )
        locked = db.execute(update(ChannelWork).where(*channel_owned(claim)).values(**values))
        if not locked.rowcount:
            raise LeaseLost()
    deadline = (instant + timedelta(seconds=seconds)).isoformat()
    changed = db.execute(
        update(Job)
        .where(*owned(claim))
        .values(
            state="failed" if terminal else "pending",
            quota_waits=job.quota_waits + (1 if waiting else 0),
            error=code,
            due_at=deadline,
            finished_at=instant.isoformat() if terminal else None,
        )
    )
    if not changed.rowcount:
        raise LeaseLost()
    if claim.channel_network and claim.kind != "youtube_subscribe":
        channel_id = claim.payload["channel_id"]
        sync, creator = db.get(ChannelSync, channel_id), db.get(Creator, channel_id)
        if sync:
            sync.error = code
            sync.next_poll_at = (instant + timedelta(hours=6)).isoformat()
        if creator:
            creator.last_error = code


def replay_job(db, job_id, expected_attempts, reason, apply=False):
    """One auditable replay per failed job. Replaying its successor requires a new explicit action."""
    original = db.get(Job, job_id)
    if not original or original.state != "failed" or original.attempts != expected_attempts:
        raise ValueError("FAILED_JOB_CHANGED")
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
