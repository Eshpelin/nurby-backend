"""add audio detections

Revision ID: b1c2d3e4f5a6
Revises: a9c3d4e5f6b7
Create Date: 2026-04-18 20:45:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'a9c3d4e5f6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audio_detections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("camera_id", sa.UUID(), nullable=False, index=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("raw_class", sa.String(128), nullable=True),
    )
    op.create_index("ix_audio_detections_camera_time", "audio_detections", ["camera_id", "detected_at"])


def downgrade() -> None:
    op.drop_index("ix_audio_detections_camera_time", table_name="audio_detections")
    op.drop_table("audio_detections")
