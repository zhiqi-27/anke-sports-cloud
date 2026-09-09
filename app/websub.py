"""YouTube WebSub transport. Notifications are hints; only Data API metadata is authoritative."""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from defusedxml import ElementTree
from sqlalchemy import delete, select

from app.config import settings
from app.content import channel_users, enqueue_channel, expire_metadata
from app.db import ChannelSync, Creator, NotificationReceipt, SessionLocal, now
from app.security import digest, problem
from app.service import enqueue

HUB = "https://pubsubhubbub.appspot.com/subscribe"
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"


def topic(channel_id):
    return "https://www.youtube.com/feeds/videos.xml?" + urlencode({"channel_id": channel_id})


def request_subscription(channel_id):
    cfg = settings()
    if not cfg.youtube_websub_enabled:
        return
    if not cfg.public_url.startswith("https://"):
        raise ValueError("PUBLIC_HTTPS_REQUIRED")
    with SessionLocal() as db:
        sync = db.get(ChannelSync, channel_id)
        if not sync:
            return
        active = bool(list(channel_users(db, channel_id)))
        mode = "subscribe" if active else "unsubscribe"
        if not active and sync.state in {"disabled", "unsubscribed", "expired"}:
            return
        if not sync.secret_ciphertext:
            sync.secret_ciphertext = cfg.cipher().encrypt(secrets.token_urlsafe(32).encode()).decode()
        sync.state = (
            ("renewing" if sync.lease_expires_at and sync.lease_expires_at > now() else "pending")
            if active
            else "unsubscribing"
        )
        sync.pending_until = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        callback = cfg.public_url.rstrip("/") + "/webhooks/youtube/" + sync.callback_id
        secret = cfg.cipher().decrypt(sync.secret_ciphertext.encode()).decode()
        db.commit()  # Intent must be visible to the asynchronous verification request.
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.post(
            HUB,
            data={
                "hub.callback": callback,
                "hub.mode": mode,
                "hub.topic": topic(channel_id),
                "hub.lease_seconds": "432000",
                "hub.secret": secret,
                "hub.verify": "async",
            },
        )
        if response.status_code not in (202, 204):
            raise ValueError("HUB_REQUEST_REJECTED")


def verify_subscription(db, callback_id, params):
    sync = db.scalar(select(ChannelSync).where(ChannelSync.callback_id == callback_id).with_for_update())
    if not sync or params.get("hub.topic") != topic(sync.channel_id):
        problem("NOT_FOUND", "未找到订阅", 404)
    if (
        not sync.pending_until
        or sync.pending_until < now()
        or sync.state not in {"pending", "renewing", "unsubscribing"}
    ):
        problem("NOT_FOUND", "没有待确认的订阅", 404)
    active = bool(list(channel_users(db, sync.channel_id)))
    expected_mode = "unsubscribe" if sync.state == "unsubscribing" else "subscribe"
    if (expected_mode == "subscribe") != active:
        problem("NOT_FOUND", "频道订阅状态已变化", 404)
    if params.get("hub.mode") == "denied":
        sync.state, sync.error, sync.pending_until = "denied", "HUB_DENIED", None
        return ""
    if params.get("hub.mode") != expected_mode:
        problem("NOT_FOUND", "未请求此订阅操作", 404)
    challenge = params.get("hub.challenge", "")
    if not challenge or len(challenge) > 2048:
        problem("INVALID_CHALLENGE", "验证内容无效")
    if expected_mode == "unsubscribe":
        sync.state, sync.pending_until, sync.lease_expires_at, sync.renew_at = (
            "unsubscribed",
            None,
            None,
            None,
        )
        return challenge
    try:
        lease = int(params.get("hub.lease_seconds", ""))
    except (ValueError, TypeError):
        problem("INVALID_LEASE", "租约无效")
    if not challenge or len(challenge) > 2048 or not 60 <= lease <= 90 * 86400:
        problem("INVALID_LEASE", "租约无效")
    start = datetime.now(timezone.utc)
    sync.state, sync.error, sync.pending_until = "verified", "", None
    sync.lease_expires_at = (start + timedelta(seconds=lease)).isoformat()
    sync.renew_at = (start + timedelta(seconds=lease * 0.8)).isoformat()
    return challenge


def notification(db, callback_id, body, signature):
    sync = db.scalar(select(ChannelSync).where(ChannelSync.callback_id == callback_id).with_for_update())
    if not sync or not sync.secret_ciphertext or not sync.lease_expires_at or sync.lease_expires_at < now():
        return False
    if not list(channel_users(db, sync.channel_id)):
        return False
    secret = settings().cipher().decrypt(sync.secret_ciphertext.encode())
    algorithm, _, supplied = (signature or "").partition("=")
    if algorithm not in {"sha1", "sha256"}:
        return False
    if not re.fullmatch(r"[0-9a-f]{40}" if algorithm == "sha1" else r"[0-9a-f]{64}", supplied):
        return False
    expected = hmac.new(secret, body, getattr(hashlib, algorithm)).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return False
    # Signature failures are acknowledged and ignored, per the Hub protocol.
    if len(body) > 65536:
        return False
    try:
        root = ElementTree.fromstring(body)
    except Exception:
        return False
    entries = root.findall(ATOM + "entry")
    if len(entries) > 50:
        return False
    ids = []
    for entry in entries:
        ident = entry.findtext(YT + "videoId", "")
        channel = entry.findtext(YT + "channelId", "")
        if channel != sync.channel_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", ident):
            return False
        ids.append(ident)
    if not ids:
        return False
    receipt_id = digest(sync.channel_id + ":" + hashlib.sha256(body).hexdigest())
    if db.get(NotificationReceipt, receipt_id):
        return True
    db.add(NotificationReceipt(id=receipt_id, channel_id=sync.channel_id))
    enqueue(db, "youtube_videos", {"channel_id": sync.channel_id, "video_ids": list(dict.fromkeys(ids))})
    sync.last_notification_at = now()
    return True


def schedule_content():
    with SessionLocal() as db:
        for creator in db.scalars(select(Creator)):
            sync = db.get(ChannelSync, creator.channel_id)
            if not sync:
                sync = ChannelSync(channel_id=creator.channel_id)
                db.add(sync)
                db.flush()
            active = bool(list(channel_users(db, creator.channel_id)))
            if not active:
                pending = sync.pending_until and sync.pending_until > now()
                if (
                    settings().youtube_websub_enabled
                    and sync.state not in {"disabled", "unsubscribed", "expired"}
                    and not pending
                ):
                    enqueue_channel(db, creator.channel_id, "youtube_subscribe")
                continue
            if sync.next_poll_at <= now():
                enqueue_channel(db, creator.channel_id)
            if sync.lease_expires_at and sync.lease_expires_at <= now():
                sync.state = "expired"
            pending_intent = sync.pending_until and sync.pending_until > now()
            renew_due = not sync.renew_at or sync.renew_at <= now()
            if settings().youtube_websub_enabled and renew_due and not pending_intent:
                enqueue_channel(db, creator.channel_id, "youtube_subscribe")
        expire_metadata(db)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        db.execute(delete(NotificationReceipt).where(NotificationReceipt.received_at < cutoff))
        db.commit()
