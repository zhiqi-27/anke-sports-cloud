"""Account erasure, durable external identity cleanup and retained tombstones."""

from sqlalchemy import delete, or_, select

from app.config import settings
from app.db import CommandReceipt, Feed, Job, JobReplay, Link, Projection, Session, User, VideoMatch, now
from app.oauth import delete_owner_connections
from app.security import problem
from app.service import active_user


def delete_account_data(db, user_id, *, firebase_project=None):
    user = active_user(db, user_id)
    if firebase_project and firebase_project != settings().firebase_project_id:
        problem("IDENTITY_TARGET_MISMATCH", "登录账号的配置已变化，请重新登录", 409)
    # This lock orders deletion against config writes, matching, publication,
    # consent, token issuance and personal job enqueueing across all transports.
    user.deleted, user.config, user.display_name = True, {}, "Deleted account"
    user.revision += 1
    db.flush()
    delete_owner_connections(db, user.id)
    db.execute(delete(CommandReceipt).where(CommandReceipt.owner_id == user.id))
    owned_jobs = select(Job.id).where(Job.payload["user_id"].as_string() == user.id)
    db.execute(
        delete(JobReplay).where(
            or_(JobReplay.source_id.in_(owned_jobs), JobReplay.new_job_id.in_(owned_jobs))
        )
    )
    db.execute(delete(Job).where(Job.payload["user_id"].as_string() == user.id))
    feed = db.scalar(select(Feed).where(Feed.owner_id == user.id).with_for_update())
    if feed:
        db.execute(delete(Projection).where(Projection.feed_id == feed.id))
        feed.revoked, feed.paused, feed.body, feed.token_ciphertext = True, True, "", ""
        feed.etag, feed.updated_at = "", now()
    db.execute(delete(Link).where(Link.owner_id == user.id))
    db.execute(delete(VideoMatch).where(VideoMatch.owner_id == user.id))
    db.execute(delete(Session).where(Session.user_id == user.id))
    if firebase_project:
        # The sole job allowed for a tombstone. Its target is fixed at the
        # authenticated request, never inferred from a later worker environment.
        db.add(
            Job(kind="identity_cleanup", payload={"user_id": user.id, "firebase_project": firebase_project})
        )
    return {
        "deleted": True,
        "identity_cleanup": "queued" if firebase_project else "not_applicable",
        "external_cache": "请在系统日历中删除旧订阅以清除缓存",
    }


def cleanup_identity(db, payload):
    from firebase_admin import auth
    from app.security import firebase_app

    owner_id = payload["user_id"]
    deleted = db.scalar(select(User.deleted).where(User.id == owner_id))
    if not deleted:
        raise ValueError("DELETION_NOT_CONFIRMED")
    project = payload.get("firebase_project")
    if not project or project != settings().firebase_project_id:
        raise ValueError("IDENTITY_TARGET_MISMATCH")
    app = firebase_app()
    # External deletion is idempotent across a crash after the HTTP side effect.
    try:
        auth.delete_user(owner_id, app=app)
    except auth.UserNotFoundError:
        pass
