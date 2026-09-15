"""Stored bounded public YouTube comment samples

Revision ID: 78a26d8eb91f
Revises: 0f6d9b742ca1
"""

from alembic import op
import sqlalchemy as sa


revision = "78a26d8eb91f"
down_revision = "0f6d9b742ca1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("videos", sa.Column("comments", sa.JSON(), nullable=True))
    op.execute(sa.text("UPDATE videos SET comments = '[]' WHERE comments IS NULL"))
    with op.batch_alter_table("videos") as batch:
        batch.alter_column("comments", existing_type=sa.JSON(), nullable=False)


def downgrade():
    with op.batch_alter_table("videos") as batch:
        batch.drop_column("comments")
