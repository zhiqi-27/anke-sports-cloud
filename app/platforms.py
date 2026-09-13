"""Candidate URL grammar and a bounded, credential-free HEAD probe.

A matching URL or HTTP response never establishes an official broadcast or playback.
"""

import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import parse_qs, urljoin, urlsplit

from app.security import canonical_url

# Explicit domains retained from the existing manual-link contract. These rules
# describe candidate pages, not a verified platform/region/device support matrix.
RULES = [
    {
        "id": "apple-tv",
        "name": "Apple TV",
        "hosts": ["tv.apple.com"],
        "paths": ["^/.+"],
        "evidence": "https://tv.apple.com/us/info/watch-f1",
    },
    {
        "id": "peacock",
        "name": "Peacock",
        "hosts": ["www.peacocktv.com"],
        "paths": ["^/.+"],
        "evidence": "https://www.peacocktv.com/sports/premier-league",
    },
    {
        "id": "tencent-sports",
        "name": "腾讯体育",
        "hosts": ["sports.qq.com", "v.qq.com"],
        "paths": ["^/.+"],
        "evidence": "https://corp.formula1.com/formula-1-renews-partnership-with-tencent-to-broadcast-f1-in-mainland-china/",
    },
    {
        "id": "migu",
        "name": "咪咕视频",
        "hosts": ["www.miguvideo.com", "m.miguvideo.com"],
        "paths": ["^/.+"],
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "id": "premier-league",
        "name": "Premier League",
        "hosts": ["www.premierleague.com"],
        "paths": ["^/.+"],
        "evidence": "https://www.premierleague.com/en/media/broadcasters",
    },
    {
        "id": "youtube",
        "name": "YouTube",
        "hosts": ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"],
        "paths": [r"^/watch$", r"^/(shorts|live|embed)/[^/]+/?$", r"^/[A-Za-z0-9_-]{11}/?$"],
        "evidence": "https://developers.google.com/youtube/v3/docs/videos",
    },
    {
        "id": "nba",
        "name": "NBA",
        "hosts": ["nba.com", "www.nba.com", "watch.nba.com", "tv.nba.com", "support.watch.nba.com"],
        "paths": [r"^/.+"],
        "evidence": "https://www.nba.com/watch/featured",
    },
    {
        "id": "f1",
        "name": "Formula 1",
        "hosts": ["f1tv.formula1.com", "www.formula1.com", "corp.formula1.com"],
        "paths": [r"^/.+"],
        "evidence": "https://f1tv.formula1.com/",
    },
    {
        "id": "uefa",
        "name": "UEFA",
        "hosts": ["www.uefa.com"],
        "paths": [r"^/.+"],
        "evidence": "https://www.uefa.com/",
    },
    {
        "id": "fifa",
        "name": "FIFA",
        "hosts": ["www.fifa.com"],
        "paths": [r"^/.+"],
        "evidence": "https://www.fifa.com/",
    },
    {
        "id": "espn",
        "name": "ESPN",
        "hosts": ["www.espn.com"],
        "paths": [r"^/.+"],
        "evidence": "https://www.espn.com/watch/",
    },
    {
        "id": "bilibili",
        "name": "bilibili",
        "hosts": ["www.bilibili.com", "bilibili.com"],
        "paths": [r"^/video/[^/]+/?$", r"^/bangumi/play/[^/]+/?$"],
        "evidence": "https://www.bilibili.com/",
    },
]


def registry():
    return [{**rule, "verification": "candidate_only", "device_support": "not_verified"} for rule in RULES]


def candidate_url(value: str):
    # Security checks happen before canonicalization can remove unsafe material.
    from urllib.parse import unquote

    parsed = urlsplit(value.strip())
    decoded_path = unquote(parsed.path).lower()
    if any(x in decoded_path for x in ["\\", "..", ".m3u8", ".mpd", ".mp4", ".m4s", ".ts/"]):
        raise ValueError("UNSAFE_CONTENT_URL")
    if any(
        x.lower().replace("-", "_")
        in {
            "token",
            "access_token",
            "auth",
            "authorization",
            "signature",
            "sig",
            "key",
            "jwt",
            "session",
            "sessionid",
            "code",
            "password",
            "expires",
            "policy",
            "credential",
            "x_amz_signature",
            "x_amz_credential",
        }
        for x in parse_qs(parsed.query, keep_blank_values=True)
    ):
        raise ValueError("CREDENTIAL_URL")
    url, platform = canonical_url(value)
    parsed = urlsplit(url)
    rule = next((x for x in RULES if parsed.hostname in x["hosts"]), None)
    if not rule or not any(re.search(pattern, parsed.path) for pattern in rule["paths"]):
        raise ValueError("UNSUPPORTED_CONTENT_PATH")
    if (
        parsed.path.rstrip("/").lower()
        in {"/login", "/signin", "/sign-in", "/subscribe", "/account", "/auth", "/watch", "/games"}
        and rule["id"] != "youtube"
    ):
        raise ValueError("CONTENT_PAGE_REQUIRED")
    return url, platform


def _public_ips(host):
    addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise ValueError("UNSAFE_ADDRESS")
    return addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=5, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the already validated IP; TLS still verifies the original host.
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            if not ipaddress.ip_address(raw.getpeername()[0]).is_global:
                raise ValueError("UNSAFE_ADDRESS")
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def head_probe(value: str) -> str:
    """No redirects, cookies, proxies, bearer headers, body storage or exception URLs."""
    connection = None
    try:
        url, _ = candidate_url(value)
        parsed = urlsplit(url)
        addresses = _public_ips(parsed.hostname)
        connection = _PinnedHTTPS(parsed.hostname, addresses[0])
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        connection.request(
            "HEAD",
            target,
            headers={
                "User-Agent": "AnkeSports-LinkCheck/0.1",
                "Accept": "text/html,application/xhtml+xml",
                "Connection": "close",
            },
        )
        response = connection.getresponse()  # http.client bounds headers; HEAD reads no body.
        if response.status in {301, 302, 303, 307, 308}:
            location = response.getheader("Location", "")
            if not location or len(location) > 2000:
                return "unsafe"
            candidate_url(urljoin(url, location))
            # Even an allowed redirect may lead to a different event. A reviewer must inspect it.
            return "redirect_review"
        if response.status in {404, 410}:
            return "not_found"
        if response.status in {401, 403, 451}:
            return "restricted"
        if response.status in {405, 501}:
            return "head_unsupported"
        if 200 <= response.status < 300:
            content_type = response.getheader("Content-Type", "").lower()
            if any(x in content_type for x in ["mpegurl", "dash+xml", "video/", "octet-stream"]):
                return "unsafe"
            return "reachable"
        return "retry"
    except (ValueError, http.client.InvalidURL):
        return "unsafe"
    except Exception as exc:
        from fastapi import HTTPException

        return "unsafe" if isinstance(exc, HTTPException) else "retry"
    finally:
        if connection:
            connection.close()
