"""person_action_segments table (HAR timeline)

Revision ID: c3f5e7a9b1d4
Revises: b2e4d6f8a0c1
Create Date: 2026-06-09 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'c3f5e7a9b1d4'
down_revision: Union[str, None] = 'b2e4d6f8a0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "person_action_segments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("camera_id", UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", UUID(as_uuid=True), nullable=True),
        sa.Column("person_name", sa.String(128), nullable=True),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("confidence_avg", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pas_camera_started", "person_action_segments", ["camera_id", "started_at"])
    op.create_index("ix_pas_person_started", "person_action_segments", ["person_id", "started_at"])
    op.create_index("ix_pas_action", "person_action_segments", ["action"])


def downgrade() -> None:
    op.drop_index("ix_pas_action", table_name="person_action_segments")
    op.drop_index("ix_pas_person_started", table_name="person_action_segments")
    op.drop_index("ix_pas_camera_started", table_name="person_action_segments")
    op.drop_table("person_action_segments")
