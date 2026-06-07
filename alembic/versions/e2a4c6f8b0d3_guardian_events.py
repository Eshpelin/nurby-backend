"""guardian events table (arrival/pickup/zone day-timeline)

Revision ID: e2a4c6f8b0d3
Revises: d1f3a5c7e9b2
Create Date: 2026-06-07 14:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'e2a4c6f8b0d3'
down_revision: Union[str, None] = 'd1f3a5c7e9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "guardian_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("person_id", UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("zone", sa.String(255), nullable=True),
        sa.Column("camera_id", UUID(as_uuid=True), nullable=True),
        sa.Column("observation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("pickup_matched", sa.Boolean(), nullable=True),
        sa.Column("pickup_name", sa.String(255), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_guardian_events_person", "guardian_events", ["person_id"])
    op.create_index("ix_guardian_events_at", "guardian_events", ["at"])


def downgrade() -> None:
    op.drop_table("guardian_events")
