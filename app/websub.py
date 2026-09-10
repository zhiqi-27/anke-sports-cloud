"""YouTube WebSub transport. Notifications are hints; only Data API metadata is authoritative."""

import secrets
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select, update

from app.config import settings
from app.content import channel_users, enqueue_channel, expire_metadata
from app.db import ChannelSync, Creator, NotificationReceipt, SessionLocal, now
from app.jobs import guard_channel_claim
from app.security import problem
from app.service import enqueue
from app.websub_rules import HUB, notification_entries, topic, verification_digest


def lock_sync(db, predicate):
    # SQLite ignores FOR UPDATE; acquire a write lock before loading mutable protocol state.
    db.execute(update(ChannelSync).where(predicate).values(state=ChannelSync.state))
    return db.scalar(
        select(ChannelSync).where(predicate).with_for_update().execution_options(populate_existing=True)
    )


def request_subscription(channel_id, claim):
    cfg = settings()
    if not cfg.youtube_websub_enabled:
        return
    if not cfg.public_url.startswith("https://"):
        raise ValueError("PUBLIC_HTTPS_REQUIRED")
    with SessionLocal() as db:
        if claim.channel != channel_id or claim.kind != "youtube_subscribe":
            raise ValueError("CHANNEL_CLAIM_MISMATCH")
        guard_channel_claim(db, claim)
        sync = lock_sync(db, ChannelSync.channel_id == channel_id)
        if not sync:
            return
        active = bool(list(channel_users(db, channel_id)))
        mode = "subscribe" if active else "unsubscribe"
        if not active and sync.state in {"disabled", "unsubscribed", "expired"}:
            return
        # A duplicate job must not replace an intent awaiting verification or renew an
        # already valid lease early. Failed HTTP responses can still have reached the Hub.
        same_intent = sync.state in ({"pending", "renewing"} if active else {"unsubscribing"})
        if same_intent and sync.pending_until and sync.pending_until > now():
            return
        if active and sync.state == "verified" and sync.renew_at and sync.renew_at > now():
            return
        if not sync.secret_ciphertext:
            sync.secret_ciphertext = cfg.cipher().encrypt(secrets.token_urlsafe(32).encode()).decode()
        sync.state = (
            ("renewing" if sync.lease_expires_at and sync.lease_expires_at > now() else "pending")
            if active
            else "unsubscribing"
        )
        sync.pending_until = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        sync.verification_digest = ""
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
            response.raise_for_status()
            raise ValueError("HUB_REQUEST_REJECTED")


def verify_subscription(db, callback_id, params):
    sync = lock_sync(db, ChannelSync.callback_id == callback_id)
    if not sync or params.get("hub.topic") != topic(sync.channel_id):
        problem("NOT_FOUND", "未找到订阅", 404)
    active = bool(list(channel_users(db, sync.channel_id)))
    challenge = params.get("hub.challenge", "")
    verification = verification_digest(params)
    if (
        challenge
        and len(challenge) <= 2048
        and sync.verification_digest == verification
        and (
            (
                active
                and sync.state == "verified"
                and sync.lease_expires_at
                and sync.lease_expires_at > now()
                and params.get("hub.mode") == "subscribe"
            )
            or (not active and sync.state == "unsubscribed" and params.get("hub.mode") == "unsubscribe")
        )
    ):
        return challenge  # Retry acknowledgement; do not extend the already negotiated lease.
    if (
        not sync.pending_until
        or sync.pending_until < now()
        or sync.state not in {"pending", "renewing", "unsubscribing"}
    ):
        problem("NOT_FOUND", "没有待确认的订阅", 404)
    expected_mode = "unsubscribe" if sync.state == "unsubscribing" else "subscribe"
    if (expected_mode == "subscribe") != active:
        problem("NOT_FOUND", "频道订阅状态已变化", 404)
    if params.get("hub.mode") == "denied":
        sync.state, sync.error, sync.pending_until = "denied", "HUB_DENIED", None
        return ""
    if params.get("hub.mode") != expected_mode:
        problem("NOT_FOUND", "未请求此订阅操作", 404)
    if not challenge or len(challenge) > 2048:
        problem("INVALID_CHALLENGE", "验证内容无效")
    if expected_mode == "unsubscribe":
        sync.state, sync.pending_until, sync.lease_expires_at, sync.renew_at = (
            "unsubscribed",
            None,
            None,
            None,
        )
        sync.verification_digest = verification
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
    sync.verification_digest = verification
    return challenge


def notification(db, callback_id, body, signature):
    sync = lock_sync(db, ChannelSync.callback_id == callback_id)
    if not sync or not sync.secret_ciphertext or not sync.lease_expires_at or sync.lease_expires_at < now():
        return False
    if not list(channel_users(db, sync.channel_id)):
        return False
    secret = settings().cipher().decrypt(sync.secret_ciphertext.encode())
    entries = notification_entries(sync.channel_id, body, signature, secret)
    if not entries:
        return False
    ids = []
    for entry in entries:
        if not db.get(NotificationReceipt, entry["key"]):
            db.add(NotificationReceipt(id=entry["key"], channel_id=sync.channel_id))
            ids.append(entry["video_id"])
    if not ids:
        return True
    enqueue(db, "youtube_videos", {"channel_id": sync.channel_id, "video_ids": list(dict.fromkeys(ids))})
    sync.last_notification_at = now()
    return True


def schedule_content():
    with SessionLocal() as db:
        for creator in db.scalars(select(Creator)):
            sync = lock_sync(db, ChannelSync.channel_id == creator.channel_id)
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
