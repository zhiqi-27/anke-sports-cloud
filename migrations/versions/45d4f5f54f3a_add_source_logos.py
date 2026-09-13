"""add source logos

Revision ID: 45d4f5f54f3a
Revises: f809a45c2d71
Create Date: 2026-09-13 21:42:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "45d4f5f54f3a"
down_revision: Union[str, Sequence[str], None] = "f809a45c2d71"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("logo_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "logo_url")
