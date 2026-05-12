"""add privacy_zones table + camera config

Revision ID: d6e7f8a3b4c5
Revises: c5d6e7f8a2b3
Create Date: 2026-05-08 20:00:00.000000

Smart Privacy Zones. AI detects bed, monitor, bathroom door, window
(etc.) per camera and blurs those regions BEFORE the frame ever
reaches storage, the VLM, or the thumbnail writer. User picks target
labels; the perception pipeline finds matching detections on every
keyframe and persists them as accepted zones the user can later
toggle / lock manually.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d6e7f8a3b4c5"
down_revision: Union[str, None] = "c5d6e7f8a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "privacy_zones",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "camera_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("label", sa.String(64), nullable=False),
        # Polygon as normalized 0-1 coords so it survives resolution
        # changes. For bbox-derived zones this is the four corners.
        sa.Column("polygon", postgresql.JSON(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default=sa.text("'auto'")),
        sa.Column("auto_score", sa.Float(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_privacy_zones_camera_active", "privacy_zones", ["camera_id", "active"])

    op.add_column(
        "cameras",
        sa.Column("privacy_zone_targets", postgresql.JSON(), nullable=True),
    )
    op.add_column(
        "cameras",
        sa.Column("privacy_zone_blur_strength", sa.Integer(), nullable=False, server_default="55"),
    )


def downgrade() -> None:
    op.drop_column("cameras", "privacy_zone_blur_strength")
    op.drop_column("cameras", "privacy_zone_targets")
    op.drop_index("ix_privacy_zones_camera_active", table_name="privacy_zones")
    op.drop_table("privacy_zones")
