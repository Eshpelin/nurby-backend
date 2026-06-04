"""add vehicles table and observation.vehicle_detections

Revision ID: b2d4f6a8c1e3
Revises: a1c3e5f7b9d2
Create Date: 2026-06-04 08:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b2d4f6a8c1e3'
down_revision: Union[str, None] = 'a1c3e5f7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicles",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_key", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("nickname", sa.String(255), nullable=True),
        sa.Column("license_plate", sa.String(32), nullable=True),
        sa.Column("vehicle_type", sa.String(32), nullable=True),
        sa.Column("make", sa.String(64), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("color", sa.String(32), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("description_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("photo_path", sa.String(1024), nullable=True),
        sa.Column("is_starred", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_provisional", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("first_camera_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("sighting_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_vehicles_identity_key", "vehicles", ["identity_key"])
    op.create_index("ix_vehicles_identity_key", "vehicles", ["identity_key"])
    op.create_index("ix_vehicles_license_plate", "vehicles", ["license_plate"])

    op.add_column(
        "observations",
        sa.Column("vehicle_detections", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("observations", "vehicle_detections")
    op.drop_index("ix_vehicles_license_plate", table_name="vehicles")
    op.drop_index("ix_vehicles_identity_key", table_name="vehicles")
    op.drop_constraint("uq_vehicles_identity_key", "vehicles", type_="unique")
    op.drop_table("vehicles")
