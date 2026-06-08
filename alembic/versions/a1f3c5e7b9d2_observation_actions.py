"""observation_actions table (structured per-person actions)

Revision ID: a1f3c5e7b9d2
Revises: d4e6f8a0b2c4
Create Date: 2026-06-08 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'a1f3c5e7b9d2'
down_revision: Union[str, None] = 'd4e6f8a0b2c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "observation_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "observation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("camera_id", UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", UUID(as_uuid=True), nullable=True),
        sa.Column("person_name", sa.String(128), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("posture", sa.String(32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_observation_actions_observation", "observation_actions", ["observation_id"])
    op.create_index("ix_observation_actions_camera", "observation_actions", ["camera_id"])
    op.create_index("ix_observation_actions_person", "observation_actions", ["person_id"])
    op.create_index("ix_observation_actions_action", "observation_actions", ["action"])
    op.create_index("ix_observation_actions_observed_at", "observation_actions", ["observed_at"])


def downgrade() -> None:
    op.drop_index("ix_observation_actions_observed_at", table_name="observation_actions")
    op.drop_index("ix_observation_actions_action", table_name="observation_actions")
    op.drop_index("ix_observation_actions_person", table_name="observation_actions")
    op.drop_index("ix_observation_actions_camera", table_name="observation_actions")
    op.drop_index("ix_observation_actions_observation", table_name="observation_actions")
    op.drop_table("observation_actions")
