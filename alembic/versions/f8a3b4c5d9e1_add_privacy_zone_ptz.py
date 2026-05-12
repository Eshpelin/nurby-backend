"""add ptz_pose + ptz pose match tolerance to privacy_zones

Revision ID: f8a3b4c5d9e1
Revises: e7f8a3b4c5d8
Create Date: 2026-05-08 22:00:00.000000

PTZ-aware privacy zones. Pan/tilt cameras break the 'blur at this
normalized polygon forever' model because the polygon in 0-1 coords
tracks the frame, not the world. A bed at (0.4, 0.5) when the
camera looks east is at (?) when the camera looks north.

Two columns added.

- ``ptz_pose`` JSON. {pan: float, tilt: float, zoom: float} captured
  at detection time. Null on fixed cameras / pre-PTZ rows.
- ``stale_after_seconds`` Integer. Freshness gate. Auto zones not
  re-detected in this window stop applying. Default 60s. Manual /
  locked zones ignore it because the user marked them permanent.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "f8a3b4c5d9e1"
down_revision: Union[str, None] = "e7f8a3b4c5d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "privacy_zones",
        sa.Column("ptz_pose", postgresql.JSON(), nullable=True),
    )
    op.add_column(
        "privacy_zones",
        sa.Column(
            "stale_after_seconds",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
    )


def downgrade() -> None:
    op.drop_column("privacy_zones", "stale_after_seconds")
    op.drop_column("privacy_zones", "ptz_pose")
