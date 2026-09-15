"""Public YouTube reads through an injected, durable project budget."""

from datetime import datetime, timezone
import re
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from app.job_rules import retry_seconds
from app.security import canonical_url, problem
from app.youtube_rules import COSTS

CHANNEL_ID = r"UC[A-Za-z0-9_-]{22}"


def request(endpoint, params, *, key, budget):
    # Never interpolate an unbudgeted endpoint or let a caller override the key.
    if endpoint not in COSTS:
        raise ValueError("YOUTUBE_ENDPOINT_NOT_BUDGETED")
    if not key:
        problem("YOUTUBE_KEY_REQUIRED", "YouTube 频道服务尚未配置，暂时无法读取创作者", 503)
    ticket = budget.reserve(endpoint)
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.get(
                "https://www.googleapis.com/youtube/v3/" + endpoint,
                params={name: value for name, value in params.items() if name != "key"},
                headers={"X-Goog-Api-Key": key},
            )
        try:
            payload = response.json()
        except ValueError:
            if response.status_code < 400:
                raise
            payload = {}
        if not isinstance(payload, dict):
            if response.status_code < 400:
                raise ValueError("INVALID_YOUTUBE_RESPONSE")
            payload = {}
        if response.status_code >= 400:
            error = payload.get("error")
            errors = error.get("errors") if isinstance(error, dict) else None
            reasons = {
                x.get("reason")
                for x in (errors if isinstance(errors, list) else [])
                if isinstance(x, dict) and isinstance(x.get("reason"), str)
            }
            if endpoint == "commentThreads" and response.status_code == 403 and "commentsDisabled" in reasons:
                return {"items": [], "comments_disabled": True}
            if response.status_code == 403 and reasons.intersection({"quotaExceeded", "dailyLimitExceeded"}):
                raise budget.upstream_wait(ticket, "YOUTUBE_QUOTA_EXHAUSTED")
            if response.status_code == 429 or reasons.intersection(
                {"rateLimitExceeded", "userRateLimitExceeded"}
            ):
                delay = retry_seconds(
                    httpx.HTTPStatusError("upstream", request=response.request, response=response),
                    1,
                    datetime.now(timezone.utc),
                )
                raise budget.upstream_wait(ticket, "YOUTUBE_RATE_LIMITED", max(60, delay))
            problem("YOUTUBE_API_UNAVAILABLE", "YouTube 暂时无法读取，请检查服务配置或稍后重试", 503)
        if response.is_redirect:
            problem("YOUTUBE_API_UNAVAILABLE", "YouTube 暂时无法读取，请稍后重试", 503)
        return payload
    except (httpx.HTTPError, ValueError):
        problem("YOUTUBE_API_UNAVAILABLE", "YouTube 暂时无法读取，请检查服务配置或稍后重试", 503)


def items(payload):
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        problem("INVALID_YOUTUBE_RESPONSE", "YouTube 返回的资料不完整，请稍后重试", 503)
    return rows


def channel_details(row, expected_id=None):
    ident = row.get("id")
    snippet, details = row.get("snippet"), row.get("contentDetails")
    name = snippet.get("title") if isinstance(snippet, dict) else None
    playlists = details.get("relatedPlaylists") if isinstance(details, dict) else None
    uploads = playlists.get("uploads") if isinstance(playlists, dict) else None
    if (
        not isinstance(ident, str)
        or not re.fullmatch(CHANNEL_ID, ident)
        or (expected_id is not None and ident != expected_id)
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(uploads, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", uploads)
    ):
        problem("INVALID_YOUTUBE_RESPONSE", "YouTube 返回的频道资料不完整，请稍后重试", 503)
    return {"channel_id": ident, "name": name[:160], "uploads_id": uploads}


def resolve_creator(value, request_json):
    value = value.strip()
    if not value or len(value) > 2000 or any(ord(c) < 32 for c in value) or "\\" in value:
        problem("INVALID_CHANNEL", "请输入 YouTube 频道或视频链接")
    if re.fullmatch(CHANNEL_ID, value):
        params = {"id": value}
    elif value.startswith("@"):
        if not re.fullmatch(r"@[^\s/@?#%]{1,100}", value):
            problem("INVALID_CHANNEL", "请输入有效的 YouTube 频道名称")
        params = {"forHandle": value}
    else:
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname in {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}
                and not parsed.username
                and not parsed.password
                and parsed.port in (None, 443)
            )
        except ValueError:
            valid = False
        if not valid:
            problem("INVALID_CHANNEL", "请输入不含凭据的 YouTube HTTPS 频道或视频链接")
        parts = unquote(parsed.path).strip("/").split("/")
        if parsed.hostname != "youtu.be" and parts[0] == "channel":
            if len(parts) < 2 or not re.fullmatch(CHANNEL_ID, parts[1]):
                problem("INVALID_CHANNEL", "请输入有效的 YouTube 频道链接")
            params = {"id": parts[1]}
        elif parsed.hostname != "youtu.be" and parts[0].startswith("@"):
            if not re.fullmatch(r"@[^\s/@?#%]{1,100}", parts[0]):
                problem("INVALID_CHANNEL", "请输入有效的 YouTube 频道链接")
            params = {"forHandle": parts[0]}
        else:
            canonical, _ = canonical_url(value)
            ident = parse_qs(urlsplit(canonical).query)["v"][0]
            videos = items(request_json("videos", {"id": ident, "part": "snippet,status"}))
            if not videos:
                problem("CHANNEL_NOT_FOUND", "无法读取此公开视频")
            if len(videos) != 1 or videos[0].get("id") != ident:
                problem("INVALID_YOUTUBE_RESPONSE", "YouTube 返回的视频身份不一致", 503)
            video = videos[0]
            snippet, status = video.get("snippet"), video.get("status")
            channel = snippet.get("channelId") if isinstance(snippet, dict) else None
            if not isinstance(status, dict) or status.get("privacyStatus") != "public":
                problem("CHANNEL_NOT_FOUND", "无法读取此公开视频")
            if not isinstance(channel, str) or not re.fullmatch(CHANNEL_ID, channel):
                problem("INVALID_YOUTUBE_RESPONSE", "YouTube 返回的频道身份不完整", 503)
            params = {"id": channel}
    channels = items(request_json("channels", {**params, "part": "snippet,contentDetails"}))
    if not channels:
        problem("CHANNEL_NOT_FOUND", "没有找到此公开频道")
    if len(channels) != 1:
        problem("INVALID_YOUTUBE_RESPONSE", "YouTube 返回的频道身份不唯一", 503)
    return channel_details(channels[0], params.get("id"))
