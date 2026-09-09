"""Inspect sanitized dead letters; replay one exact failed job with an explicit apply flag."""

import argparse
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import Job, SessionLocal
from app.jobs import replay_job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, choices=["local", "staging", "production"])
    parser.add_argument("--job-id")
    parser.add_argument("--expected-attempts", type=int)
    parser.add_argument("--reason", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.environment != settings().env:
        parser.error("ENVIRONMENT_MISMATCH")
    if args.apply and not args.job_id:
        parser.error("EXACT_JOB_REQUIRED")
    with SessionLocal() as db:
        if not args.job_id:
            rows = db.scalars(
                select(Job).where(Job.state == "failed").order_by(Job.created_at.desc()).limit(50)
            )
            print(
                json.dumps(
                    {
                        "environment": args.environment,
                        "items": [
                            {
                                "id": row.id,
                                "kind": row.kind,
                                "attempts": row.attempts,
                                "error": row.error,
                                "created_at": row.created_at,
                            }
                            for row in rows
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            return
        if args.expected_attempts is None:
            parser.error("EXPECTED_ATTEMPTS_REQUIRED")
        try:
            result = replay_job(db, args.job_id, args.expected_attempts, args.reason, args.apply)
            db.commit() if args.apply else db.rollback()
        except (ValueError, IntegrityError) as exc:
            db.rollback()
            parser.error(str(exc) if isinstance(exc, ValueError) else "REPLAY_CONFLICT_REFRESH_FIRST")
        print(json.dumps({"environment": args.environment, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
