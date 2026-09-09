"""Preserve exact identifiers under MySQL regardless of database default collation."""

from alembic import op

revision = "f809a45c2d71"
down_revision = "e42c08f771d3"
branch_labels = None
depends_on = None

# Frozen inventory at this revision; do not import evolving application metadata.
TABLES = (
    "broadcast_audit",
    "broadcast_records",
    "channel_sync",
    "channel_work",
    "command_receipts",
    "creators",
    "events",
    "feeds",
    "job_replays",
    "links",
    "notification_receipts",
    "oauth_clients",
    "oauth_grants",
    "oauth_requests",
    "oauth_tokens",
    "outbox",
    "projections",
    "provider_states",
    "public_feeds",
    "sessions",
    "sources",
    "users",
    "video_matches",
    "videos",
    "youtube_budgets",
)


def upgrade():
    if op.get_context().dialect.name == "mysql":
        for table in TABLES:
            op.execute(f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin")


def downgrade():
    # Intentionally retain exact comparisons: reverting to case-insensitive
    # collation can merge distinct identities or fail halfway through MySQL DDL.
    # Older application/schema revisions remain compatible with these tables.
    pass
