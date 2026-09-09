"""indexed broadcast scheduling

Revision ID: c72b961e430a
Revises: a8c502e7d134
"""

from alembic import op
import sqlalchemy as sa

revision = "c72b961e430a"
down_revision = "a8c502e7d134"
branch_labels = None
depends_on = None


def upgrade():
    # Older published timestamps may have offsets. Let the scheduler normalize
    # them in bounded transactions rather than assuming database timezone tables.
    op.add_column(
        "broadcast_records",
        sa.Column("expires_at", sa.String(40), nullable=False, server_default=""),
    )
    op.create_index("ix_broadcast_expiry", "broadcast_records", ["status", "expires_at", "link_id"])
    op.create_index("ix_broadcast_due", "broadcast_records", ["status", "next_check_at", "link_id"])
    # Reconsider existing publications once, including the pre-match hourly window.
    op.execute(sa.text("UPDATE broadcast_records SET next_check_at = '' WHERE status = 'published'"))


def downgrade():
    op.drop_index("ix_broadcast_due", table_name="broadcast_records")
    op.drop_index("ix_broadcast_expiry", table_name="broadcast_records")
    op.drop_column("broadcast_records", "expires_at")
