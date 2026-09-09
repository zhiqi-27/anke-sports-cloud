"""HTTP validators for already-published calendars; no identity or background work."""

from datetime import datetime
from email.utils import format_datetime, parsedate_to_datetime

from fastapi import Response

from app.security import problem


def calendar_response(feed, request, *, public=False):
    if not feed.body:
        problem("FEED_BUILDING", "订阅源尚未发布，请稍后重试", 503)
    etag = f'"{feed.etag}"'
    changed = datetime.fromisoformat(feed.updated_at).replace(microsecond=0)
    headers = {
        "ETag": etag,
        "Last-Modified": format_datetime(changed, usegmt=True),
        "Cache-Control": "public, max-age=0, must-revalidate" if public else "private, no-cache",
        "X-Robots-Tag": "noindex, nofollow",
    }
    incoming = request.headers.get("if-none-match")
    unmodified = incoming and any(x.strip().removeprefix("W/") in {etag, "*"} for x in incoming.split(","))
    if not incoming and request.headers.get("if-modified-since"):
        try:
            unmodified = parsedate_to_datetime(request.headers["if-modified-since"]) >= changed
        except (TypeError, ValueError):
            pass
    if unmodified:
        return Response(status_code=304, headers=headers)
    headers["Content-Length"] = str(len(feed.body.encode()))
    if public:
        headers["Content-Disposition"] = 'attachment; filename="anke-sports-public.ics"'
    return Response(
        "" if request.method == "HEAD" else feed.body,
        media_type="text/calendar; charset=utf-8",
        headers=headers,
    )
