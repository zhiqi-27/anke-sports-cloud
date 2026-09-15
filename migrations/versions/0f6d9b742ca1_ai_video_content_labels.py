"""AI video content labels

Revision ID: 0f6d9b742ca1
Revises: 45d4f5f54f3a
"""

from alembic import op
import sqlalchemy as sa


revision = "0f6d9b742ca1"
down_revision = "45d4f5f54f3a"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("links", "video_matches"):
        op.add_column(table, sa.Column("content_labels", sa.JSON(), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET content_labels = '[]' WHERE content_labels IS NULL"))
        with op.batch_alter_table(table) as batch:
            batch.alter_column("content_labels", existing_type=sa.JSON(), nullable=False)


def downgrade():
    for table in ("video_matches", "links"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("content_labels")
