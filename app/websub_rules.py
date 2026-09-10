"""Shared protocol validation; notifications are hints, never authoritative metadata."""

from datetime import datetime
import hashlib
import hmac
import json
import re
from urllib.parse import urlencode, urlsplit

from defusedxml import ElementTree

HUB = "https://pubsubhubbub.appspot.com/subscribe"
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"


def topic(channel_id):
    return "https://www.youtube.com/feeds/videos.xml?" + urlencode({"channel_id": channel_id})


def callback_url(public_url, callback_id):
    parsed = urlsplit(public_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("PUBLIC_HTTPS_REQUIRED")
    return public_url.rstrip("/") + "/webhooks/youtube/" + callback_id


def verification_digest(params):
    return hashlib.sha256(
        json.dumps(
            [params.get(k, "") for k in ("hub.mode", "hub.topic", "hub.challenge", "hub.lease_seconds")],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def notification_entries(channel_id, body, signature, secret):
    if len(body) > 65536:
        return []
    algorithm, _, supplied = (signature or "").partition("=")
    if algorithm not in {"sha1", "sha256"} or not re.fullmatch(
        r"[0-9a-f]{40}" if algorithm == "sha1" else r"[0-9a-f]{64}", supplied
    ):
        return []
    if not hmac.compare_digest(hmac.new(secret, body, getattr(hashlib, algorithm)).hexdigest(), supplied):
        return []
    try:
        root = ElementTree.fromstring(body)
        if root.tag != ATOM + "feed":
            return []
        entries = root.findall(ATOM + "entry")
        if not 1 <= len(entries) <= 50:
            return []
        result = {}
        for entry in entries:
            ident, channel = entry.findtext(YT + "videoId", ""), entry.findtext(YT + "channelId", "")
            if channel != channel_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", ident):
                return []
            version = entry.findtext(ATOM + "updated", "").strip()
            if version:
                instant = datetime.fromisoformat(version.replace("Z", "+00:00"))
                if instant.tzinfo is None:
                    return []
                # Keep upstream fractional precision; parsing is only validation.
            else:
                # Older Hub payloads may omit updated. Canonical content remains an opaque hint.
                version = hashlib.sha256(
                    json.dumps(
                        [
                            (node.tag, (node.text or "").strip(), sorted(node.attrib.items()))
                            for node in entry.iter()
                        ],
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest()
            key = hashlib.sha256((channel_id + ":" + ident + ":" + version).encode()).hexdigest()
            result[key] = {"key": key, "video_id": ident}
        return list(result.values())
    except Exception:
        return []
