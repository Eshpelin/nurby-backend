"""add cameras.timezone and use it for daily digest scheduling

Revision ID: e7f8a3b4c5d8
Revises: d6e7f8a3b4c5
Create Date: 2026-05-08 21:00:00.000000

Per-camera IANA timezone string. Default null = use the system
timezone. Used to render timestamps in the user's local sense and
to anchor the daily digest scheduler. New cameras get the host's
default; user can override at add-time or edit later.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e7f8a3b4c5d8"
down_revision: Union[str, None] = "d6e7f8a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column("timezone", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cameras", "timezone")
