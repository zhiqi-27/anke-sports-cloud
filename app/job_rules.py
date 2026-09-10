"""Safe error labels and upstream retry delays without persistence dependencies."""

from email.utils import parsedate_to_datetime
import re

import httpx
from fastapi import HTTPException


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


def retry_seconds(exc, attempt, instant, wait_codes=()):
    seconds = min(600, 2**attempt * 10)
    if (
        isinstance(exc, HTTPException)
        and isinstance(exc.detail, dict)
        and exc.detail.get("code") in wait_codes
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
