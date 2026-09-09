"""channel work leases and verification acknowledgement

Revision ID: a8c502e7d134
Revises: 7229fa56d28e
"""

from alembic import op
import sqlalchemy as sa

revision = "a8c502e7d134"
down_revision = "7229fa56d28e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "channel_work",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("lease_job_id", sa.String(64), nullable=True),
        sa.Column("lease_attempt", sa.Integer(), nullable=True),
        sa.Column("lease_until", sa.String(40), nullable=True),
        sa.Column("next_attempt_at", sa.String(40), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(80), nullable=False, server_default=""),
    )
    op.add_column(
        "channel_sync", sa.Column("verification_digest", sa.String(64), nullable=False, server_default="")
    )


def downgrade():
    op.drop_column("channel_sync", "verification_digest")
    op.drop_table("channel_work")
