"""Complete, validated YouTube metadata batches for either persistence adapter."""

import re

from app.matching import parse_time
from app.youtube_transport import items

MAX_AI_COMMENTS = 12
MAX_AI_COMMENT_CHARS = 280
MAX_AI_COMMENTS_CHARS = 2400


def uploads_page(payload, cutoff, seen):
    rows = items(payload)
    if len(rows) > 50:
        raise ValueError("INVALID_UPLOADS_RESPONSE")
    ids, reached = [], False
    for row in rows:
        details = row.get("contentDetails")
        ident = details.get("videoId") if isinstance(details, dict) else None
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", ident):
            raise ValueError("INVALID_UPLOADS_RESPONSE")
        published = details.get("videoPublishedAt")
        if published and parse_time(published) < parse_time(cutoff):
            reached = True
        else:
            ids.append(ident)
    cursor = payload.get("nextPageToken")
    if cursor and (not isinstance(cursor, str) or len(cursor) > 1024):
        raise ValueError("INVALID_UPLOADS_RESPONSE")
    if cursor and not reached and (cursor in seen or len(seen) >= 100):
        raise ValueError("PAGINATION_LOOP")
    return list(dict.fromkeys(ids)), cursor if not reached else None


def video_batch(channel_id, ids, payload, existing, stamp):
    if len(ids) > 50 or any(not re.fullmatch(r"[A-Za-z0-9_-]{11}", ident) for ident in ids):
        raise ValueError("VIDEO_BATCH_TOO_LARGE_OR_INVALID")
    rows = items(payload)
    found = {row.get("id"): row for row in rows}
    if len(found) != len(rows) or set(found) - set(ids):
        raise ValueError("UNEXPECTED_VIDEO_ID")
    result = []
    for ident in ids:
        raw, previous = found.get(ident), existing.get(ident)
        if previous and previous["channel_id"] != channel_id:
            raise ValueError("CHANNEL_ID_MISMATCH")
        if not raw and not previous:
            continue
        if raw:
            snippet, status = raw.get("snippet"), raw.get("status")
            if not isinstance(snippet, dict) or snippet.get("channelId") != channel_id:
                raise ValueError("CHANNEL_ID_MISMATCH")
            if not isinstance(status, dict) or status.get("privacyStatus") not in {
                "public",
                "private",
                "unlisted",
            }:
                raise ValueError("INVALID_VIDEO_RESPONSE")
            published = snippet.get("publishedAt")
            title, description = snippet.get("title"), snippet.get("description", "")
            if (
                not isinstance(published, str)
                or not isinstance(title, str)
                or not isinstance(description, str)
            ):
                raise ValueError("INVALID_VIDEO_RESPONSE")
            parse_time(published)
            public = status["privacyStatus"] == "public"
        else:
            public, published = False, previous["published_at"]
        result.append(
            {
                "id": ident,
                "channel_id": channel_id,
                "title": title[:300] if public else "视频不可用",
                "description": description[:10000] if public else "",
                "published_at": published,
                "available": public,
                "updated_at": stamp,
            }
        )
    return result


def comment_sample(payload):
    """Return bounded public top-level comment text without author metadata."""
    rows = items(payload)
    if len(rows) > MAX_AI_COMMENTS:
        raise ValueError("INVALID_COMMENTS_RESPONSE")
    result, seen, used = [], set(), 0
    for row in rows:
        snippet = row.get("snippet")
        top = snippet.get("topLevelComment") if isinstance(snippet, dict) else None
        comment = top.get("snippet") if isinstance(top, dict) else None
        text = comment.get("textDisplay") if isinstance(comment, dict) else None
        if not isinstance(text, str):
            raise ValueError("INVALID_COMMENTS_RESPONSE")
        normalized = " ".join(text.split())[:MAX_AI_COMMENT_CHARS]
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        if used + len(normalized) > MAX_AI_COMMENTS_CHARS:
            break
        result.append(normalized)
        seen.add(key)
        used += len(normalized)
    return result


def search_video_ids(payload, limit=25):
    """Validate one YouTube search page and return canonical video IDs only."""
    rows = items(payload)
    if len(rows) > limit:
        raise ValueError("INVALID_SEARCH_RESPONSE")
    result = []
    for row in rows:
        ident = row.get("id")
        video_id = ident.get("videoId") if isinstance(ident, dict) else None
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError("INVALID_SEARCH_RESPONSE")
        result.append(video_id)
    return list(dict.fromkeys(result))


def discovered_video_batch(ids, payload, stamp):
    """Validate a mixed-channel search hydration response."""
    if len(ids) > 50 or any(not re.fullmatch(r"[A-Za-z0-9_-]{11}", ident) for ident in ids):
        raise ValueError("VIDEO_BATCH_TOO_LARGE_OR_INVALID")
    rows = items(payload)
    found = {row.get("id"): row for row in rows}
    if len(found) != len(rows) or set(found) - set(ids):
        raise ValueError("UNEXPECTED_VIDEO_ID")
    result = []
    for ident in ids:
        raw = found.get(ident)
        if not raw:
            continue
        snippet, status, statistics = raw.get("snippet"), raw.get("status"), raw.get("statistics", {})
        if not isinstance(snippet, dict) or not isinstance(status, dict) or not isinstance(statistics, dict):
            raise ValueError("INVALID_VIDEO_RESPONSE")
        channel_id = snippet.get("channelId")
        channel_name = snippet.get("channelTitle", "")
        published = snippet.get("publishedAt")
        title, description = snippet.get("title"), snippet.get("description", "")
        if (
            not isinstance(channel_id, str)
            or not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", channel_id)
            or not isinstance(channel_name, str)
            or not isinstance(published, str)
            or not isinstance(title, str)
            or not isinstance(description, str)
            or status.get("privacyStatus") not in {"public", "private", "unlisted"}
        ):
            raise ValueError("INVALID_VIDEO_RESPONSE")
        parse_time(published)
        try:
            views = int(statistics.get("viewCount", 0))
        except (TypeError, ValueError):
            raise ValueError("INVALID_VIDEO_RESPONSE") from None
        if views < 0:
            raise ValueError("INVALID_VIDEO_RESPONSE")
        public = status["privacyStatus"] == "public"
        result.append(
            {
                "id": ident,
                "channel_id": channel_id,
                "channel_name": channel_name[:160],
                "title": title[:300] if public else "视频不可用",
                "description": description[:10000] if public else "",
                "published_at": published,
                "view_count": views,
                "comments": [],
                "available": public,
                "updated_at": stamp,
            }
        )
    return result
