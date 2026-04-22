"""add camera display_order and webcam_device

Revision ID: 4b1118a5954c
Revises: a5da330da5c1
Create Date: 2026-04-22 17:56:57.999400
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '4b1118a5954c'
down_revision: Union[str, None] = 'a5da330da5c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "cameras",
        sa.Column("webcam_device", sa.String(length=255), nullable=True),
    )
    # Seed display_order by created_at so existing rows keep their order.
    op.execute(
        """
        WITH ordered AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY created_at) AS rn
            FROM cameras
        )
        UPDATE cameras c SET display_order = ordered.rn
        FROM ordered WHERE ordered.id = c.id
        """
    )


def downgrade() -> None:
    op.drop_column("cameras", "webcam_device")
    op.drop_column("cameras", "display_order")
