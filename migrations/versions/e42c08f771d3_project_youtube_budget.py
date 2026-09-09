"""Shared YouTube budget and capacity waits without resetting claim generations."""

from alembic import op
import sqlalchemy as sa

revision = "e42c08f771d3"
down_revision = "c72b961e430a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "youtube_budgets",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("period", sa.String(10), nullable=False),
        sa.Column("daily_limit", sa.Integer(), nullable=False),
        sa.Column("reserved_units", sa.Integer(), nullable=False),
        sa.Column("blocked_until", sa.String(40), nullable=True),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    op.add_column("outbox", sa.Column("quota_waits", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("outbox", "quota_waits")
    op.drop_table("youtube_budgets")
