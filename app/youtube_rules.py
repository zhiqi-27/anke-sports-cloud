"""YouTube quota windows and safe waits, independent of storage."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

WAIT_CODES = {
    "YOUTUBE_BUDGET_EXHAUSTED",
    "YOUTUBE_SEARCH_BUDGET_EXHAUSTED",
    "YOUTUBE_QUOTA_EXHAUSTED",
    "YOUTUBE_RATE_LIMITED",
}
NETWORK_JOBS = {"youtube_poll", "youtube_videos", "youtube_channel_metadata"}
COSTS = {"channels": 1, "playlistItems": 1, "videos": 1, "commentThreads": 1, "search": 1}


@dataclass(frozen=True)
class Reservation:
    project: str
    period: str
    reset: datetime
    endpoint: str


def window(instant):
    local = instant.astimezone(ZoneInfo("America/Los_Angeles"))
    reset = datetime.combine(local.date() + timedelta(days=1), time(), tzinfo=local.tzinfo)
    return local.date().isoformat(), reset.astimezone(timezone.utc)


def wait_error(code, resume, instant):
    return HTTPException(
        503,
        {
            "code": code,
            "message": "YouTube 请求额度暂不可用，将在允许时自动重试；已有日历继续保留",
            "retryable": True,
            "resume_at": resume.isoformat(),
            "retry_after_seconds": max(1, int((resume - instant).total_seconds()) + 1),
        },
    )
