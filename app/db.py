from datetime import datetime, timezone
from pathlib import Path
import ssl
import time
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Integer,
    Index,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return uuid4().hex


# Firebase subjects and external IDs are exact values. MySQL's default
# accent/case-insensitive collation must never collapse two account identities.
MYSQL_TABLE_OPTIONS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_bin"}


class Base(DeclarativeBase):
    __table_args__ = MYSQL_TABLE_OPTIONS


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    short_name: Mapped[str] = mapped_column(String(40))
    sport: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(30))
    color: Mapped[str] = mapped_column(String(10))
    provider: Mapped[str] = mapped_column(String(40))
    demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    source_key: Mapped[str] = mapped_column(String(220), unique=True)
    competition_id: Mapped[str] = mapped_column(String(160), index=True)
    sport: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(240))
    starts_at: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    local_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    time_precision: Mapped[str] = mapped_column(String(16), default="exact")
    timezone: Mapped[str] = mapped_column(String(80), default="UTC")
    duration: Mapped[int] = mapped_column(Integer, default=120)
    venue: Mapped[str] = mapped_column(String(240), default="")
    status: Mapped[str] = mapped_column(String(24), default="scheduled")
    participants: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(40))
    source_url: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(40), default=now)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), default="Sports fan")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    expires_at: Mapped[str] = mapped_column(String(40))


class CommandReceipt(Base):
    __tablename__ = "command_receipts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    operation: Mapped[str] = mapped_column(String(80))
    payload_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)


class Feed(Base):
    __tablename__ = "feeds"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(128), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_ciphertext: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    body: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"), default="")
    etag: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default=now)


class PublicFeed(Base):
    __tablename__ = "public_feeds"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(160), unique=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    body: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"), default="")
    etag: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default=now)
    scheduled_on: Mapped[str] = mapped_column(String(10), default="")


class Projection(Base):
    __tablename__ = "projections"
    __table_args__ = (UniqueConstraint("feed_id", "event_id"), MYSQL_TABLE_OPTIONS)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    feed_id: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)
    removed: Mapped[bool] = mapped_column(Boolean, default=False)


class Link(Base):
    __tablename__ = "links"
    __table_args__ = (UniqueConstraint("owner_id", "event_id", "url_hash"), MYSQL_TABLE_OPTIONS)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(24))
    platform: Mapped[str] = mapped_column(String(100))
    channel_id: Mapped[str] = mapped_column(String(80), default="")
    creator: Mapped[str] = mapped_column(String(140), default="")
    origin: Mapped[str] = mapped_column(String(24), default="manual")
    access: Mapped[str] = mapped_column(String(30), default="unknown")
    regions: Mapped[list] = mapped_column(JSON, default=list)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class BroadcastRecord(Base):
    __tablename__ = "broadcast_records"
    __table_args__ = (
        Index("ix_broadcast_expiry", "status", "expires_at", "link_id"),
        Index("ix_broadcast_due", "status", "next_check_at", "link_id"),
        MYSQL_TABLE_OPTIONS,
    )
    link_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    draft: Mapped[dict] = mapped_column(JSON)
    published: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    published_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    device_tests: Mapped[list] = mapped_column(JSON, default=list)
    network_status: Mapped[str] = mapped_column(String(40), default="not_checked")
    network_checked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    next_check_at: Mapped[str] = mapped_column(String(40), default=now, index=True)
    # Empty means an older publication still needs bounded UTC normalization.
    expires_at: Mapped[str] = mapped_column(String(40), default="", server_default="")
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)


class BroadcastAudit(Base):
    __tablename__ = "broadcast_audit"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    link_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class Creator(Base):
    __tablename__ = "creators"
    channel_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    uploads_id: Mapped[str] = mapped_column(String(100), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default=now)
    last_error: Mapped[str] = mapped_column(String(80), default="")


class Video(Base):
    __tablename__ = "videos"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40), default=now)
    available: Mapped[bool] = mapped_column(Boolean, default=True)


class ChannelSync(Base):
    __tablename__ = "channel_sync"
    channel_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    last_success: Mapped[str | None] = mapped_column(String(40), nullable=True)
    next_poll_at: Mapped[str] = mapped_column(String(40), default=now)
    callback_id: Mapped[str] = mapped_column(String(64), unique=True, default=uid)
    secret_ciphertext: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(24), default="disabled")
    pending_until: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lease_expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    renew_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_notification_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error: Mapped[str] = mapped_column(String(80), default="")
    verification_digest: Mapped[str] = mapped_column(String(64), default="", server_default="")


class ChannelWork(Base):
    __tablename__ = "channel_work"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    lease_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_until: Mapped[str | None] = mapped_column(String(40), nullable=True)
    next_attempt_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str] = mapped_column(String(80), default="", server_default="")


class VideoMatch(Base):
    __tablename__ = "video_matches"
    __table_args__ = (UniqueConstraint("owner_id", "video_id", "event_id"), MYSQL_TABLE_OPTIONS)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    video_id: Mapped[str] = mapped_column(String(40), index=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    decision: Mapped[str] = mapped_column(String(24))
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    rule_version: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40), default=now)


class NotificationReceipt(Base):
    __tablename__ = "notification_receipts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(80), index=True)
    received_at: Mapped[str] = mapped_column(String(40), default=now)


class OAuthClient(Base):
    __tablename__ = "oauth_clients"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    metadata_ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, default=lambda: int(time.time()))


class OAuthRequest(Base):
    __tablename__ = "oauth_requests"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(200), index=True)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    params: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    code_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    code_expires_at: Mapped[int] = mapped_column(BigInteger, default=0)


class OAuthGrant(Base):
    __tablename__ = "oauth_grants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    client_id: Mapped[str] = mapped_column(String(200))
    scopes: Mapped[list] = mapped_column(JSON)
    resource: Mapped[str] = mapped_column(String(500))
    issuer: Mapped[str] = mapped_column(String(500))
    expires_at: Mapped[int] = mapped_column(BigInteger)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class OAuthTokenRecord(Base):
    __tablename__ = "oauth_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    grant_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    scopes: Mapped[list] = mapped_column(JSON)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Job(Base):
    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_ready", "state", "due_at", "created_at"), MYSQL_TABLE_OPTIONS)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    quota_waits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    due_at: Mapped[str] = mapped_column(String(40), default=now)
    error: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class JobReplay(Base):
    __tablename__ = "job_replays"
    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    new_job_id: Mapped[str] = mapped_column(String(64), unique=True)
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class YouTubeBudget(Base):
    __tablename__ = "youtube_budgets"
    project_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    period: Mapped[str] = mapped_column(String(10))
    daily_limit: Mapped[int] = mapped_column(Integer)
    reserved_units: Mapped[int] = mapped_column(Integer, default=0)
    blocked_until: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default=now)


class ProviderState(Base):
    __tablename__ = "provider_states"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_success: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error: Mapped[str] = mapped_column(String(100), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_attempt_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lease_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_until: Mapped[str | None] = mapped_column(String(40), nullable=True)


url = settings().database_url
if settings().env == "local":
    Path("data").mkdir(exist_ok=True)
connection_args = (
    {"check_same_thread": False}
    if url.startswith("sqlite")
    else ({"ssl": ssl.create_default_context()} if settings().env != "local" else {})
)


def engine_options(database_url):
    # Handlers reread versions after network/owner locks; these reads must see
    # committed changes rather than an earlier MySQL repeatable-read snapshot.
    from sqlalchemy.engine import make_url

    options = {"pool_pre_ping": True, "hide_parameters": True}
    if make_url(database_url).get_backend_name() == "mysql":
        options["isolation_level"] = "READ COMMITTED"
    return options


engine = create_engine(url, **engine_options(url), connect_args=connection_args)
if url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def sqlite_pragmas(connection, _):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")


SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session
