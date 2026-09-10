"""Project-wide durable request reservations. Never equate this ledger with Google usage.

All callers must reach HTTP before acquiring business write locks. Reservations
commit in a separate session so a failed/rolled-back business operation cannot
refund a request already sent upstream. Keys never identify the budget bucket.
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app import db as database
from app.config import settings
from app.db import YouTubeBudget

from app.youtube_rules import (
    COSTS,
    NETWORK_JOBS as NETWORK_JOBS,
    WAIT_CODES as WAIT_CODES,
    Reservation,
    wait_error as shared_wait_error,
    window as window,
)


def clock():
    return datetime.now(timezone.utc)


def configured():
    from app.providers import provider_key

    return bool(settings().youtube_project_id and provider_key("YOUTUBE_API_KEY"))


def wait_error(code, resume):
    return shared_wait_error(code, resume, clock())


def lock_row(db, project, instant):
    period, _ = window(instant)
    if db.bind.dialect.name == "mysql":
        # A duplicate INSERT leaves a shared record lock under InnoDB. Multiple
        # contenders upgrading that lock to UPDATE can deadlock. This upsert
        # obtains the exclusive row lock directly and preserves existing usage.
        from sqlalchemy.dialects.mysql import insert

        db.execute(
            insert(YouTubeBudget)
            .values(
                project_id=project,
                period=period,
                daily_limit=settings().youtube_daily_budget,
                reserved_units=0,
            )
            .on_duplicate_key_update(project_id=project)
        )
    elif not db.get(YouTubeBudget, project):
        try:
            with db.begin_nested():
                db.add(
                    YouTubeBudget(
                        project_id=project,
                        period=period,
                        daily_limit=settings().youtube_daily_budget,
                        reserved_units=0,
                    )
                )
                db.flush()
        except IntegrityError:
            pass
    db.execute(
        update(YouTubeBudget)
        .where(YouTubeBudget.project_id == project)
        .values(reserved_units=YouTubeBudget.reserved_units)
    )
    return db.scalar(
        select(YouTubeBudget)
        .where(YouTubeBudget.project_id == project)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def reserve(endpoint):
    if endpoint not in COSTS:
        raise ValueError("YOUTUBE_ENDPOINT_NOT_BUDGETED")
    cfg = settings()
    if not cfg.youtube_project_id:
        raise HTTPException(
            503,
            {
                "code": "YOUTUBE_PROJECT_REQUIRED",
                "message": "请先配置独立 YouTube 项目 ID",
                "retryable": False,
            },
        )
    instant = clock()
    period, reset = window(instant)
    error = None
    with database.SessionLocal() as db:
        row = lock_row(db, cfg.youtube_project_id, instant)
        if row.period > period:
            raise ValueError("YOUTUBE_CLOCK_MOVED_BACKWARD")
        if row.period < period:
            row.period, row.reserved_units, row.daily_limit = period, 0, cfg.youtube_daily_budget
        else:
            # Rolling deployments with different settings obey the smaller cap.
            # Budget increases become effective at the next Pacific-day rollover.
            row.daily_limit = min(row.daily_limit, cfg.youtube_daily_budget)
        if row.blocked_until and row.blocked_until > instant.isoformat():
            error = wait_error(row.reason, datetime.fromisoformat(row.blocked_until))
        elif row.reserved_units + COSTS[endpoint] > row.daily_limit:
            row.blocked_until, row.reason = reset.isoformat(), "YOUTUBE_BUDGET_EXHAUSTED"
            error = wait_error(row.reason, reset)
        else:
            row.reserved_units += COSTS[endpoint]
            row.blocked_until, row.reason = None, ""
        row.updated_at = instant.isoformat()
        db.commit()
    if error:
        raise error
    return Reservation(cfg.youtube_project_id, period, reset)


def upstream_wait(ticket, code, seconds=60):
    instant = clock()
    period, reset = window(instant)
    resume = reset if code == "YOUTUBE_QUOTA_EXHAUSTED" else instant + timedelta(seconds=seconds)
    # A quota response from yesterday must not disable today's new quota bucket.
    if code == "YOUTUBE_QUOTA_EXHAUSTED" and ticket.period != period:
        return wait_error(code, instant + timedelta(seconds=60))
    with database.SessionLocal() as db:
        row = lock_row(db, ticket.project, instant)
        if not row.blocked_until or row.blocked_until < resume.isoformat():
            row.blocked_until, row.reason = resume.isoformat(), code
        row.updated_at = instant.isoformat()
        db.commit()
    return wait_error(code, resume)


def status(db):
    cfg = settings()
    instant = clock()
    period, reset = window(instant)
    result = {
        "configured": configured(),
        "state": "unconfigured",
        "daily_limit": cfg.youtube_daily_budget,
        "reserved_units": 0,
        "available_units": None,
        "reset_at": None,
        "resume_at": None,
    }
    if not result["configured"]:
        return result
    row = db.scalar(
        select(YouTubeBudget)
        .where(YouTubeBudget.project_id == cfg.youtube_project_id)
        .execution_options(populate_existing=True)
    )
    current = row is not None and row.period >= period
    limit = min(row.daily_limit, cfg.youtube_daily_budget) if current else cfg.youtube_daily_budget
    used = row.reserved_units if current else 0
    resume = (
        row.blocked_until if row and row.blocked_until and row.blocked_until > instant.isoformat() else None
    )
    if used >= limit and not resume:
        resume = reset.isoformat()
    result.update(
        state="waiting" if resume else "available",
        daily_limit=limit,
        reserved_units=used,
        available_units=max(0, limit - used),
        reset_at=reset.isoformat(),
        resume_at=resume,
    )
    return result
