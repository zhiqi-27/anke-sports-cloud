"""store completed team-sport results separately from canonical titles

Revision ID: b7e1f3c9a4d2
Revises: 78a26d8eb91f
"""

from alembic import op
import sqlalchemy as sa


revision = "b7e1f3c9a4d2"
down_revision = "78a26d8eb91f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("result", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("events", "result")
