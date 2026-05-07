"""add vlm refiner cascade fields

Revision ID: e1f2a3b4c5d7
Revises: d9e1f2a3b4c5
Create Date: 2026-05-07 21:00:00.000000

VLM cascade. The primary VLM (cheap, e.g. local Gemma) describes
every keyframe. The refiner (expensive, e.g. Claude) only fires when
configured triggers match. Triggers are YOLO labels seen + keywords
in the primary's text output.

Off by default. Camera owner explicitly turns it on with
``vlm_refiner_provider_id`` and chooses triggers. Existing token
caps + LLMErrorToasts cover the cost-runaway story.

Observations gain ``primary_vlm_description`` so the UI can show the
before/after comparison popover. The original ``vlm_description``
column always carries the latest text (primary on first pass,
refiner replacement when escalation fires).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e1f2a3b4c5d7"
down_revision: Union[str, None] = "d9e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column(
            "vlm_refiner_provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "cameras",
        sa.Column("vlm_refiner_trigger_objects", postgresql.JSON(), nullable=True),
    )
    op.add_column(
        "cameras",
        sa.Column("vlm_refiner_keywords", postgresql.JSON(), nullable=True),
    )
    op.add_column(
        "cameras",
        sa.Column("vlm_refiner_max_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "cameras",
        sa.Column("vlm_refiner_max_input_tokens", sa.Integer(), nullable=True),
    )

    # Observation history of the cascade. primary_vlm_description holds
    # the cheap-model output when an escalation replaced it. The
    # ``vlm_description`` column always shows the latest. refined_*
    # columns let the UI render the badge + popover.
    op.add_column(
        "observations",
        sa.Column("primary_vlm_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "observations",
        sa.Column("refined_by_provider_name", sa.String(64), nullable=True),
    )
    op.add_column(
        "observations",
        sa.Column("refined_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("observations", "refined_at")
    op.drop_column("observations", "refined_by_provider_name")
    op.drop_column("observations", "primary_vlm_description")
    op.drop_column("cameras", "vlm_refiner_max_input_tokens")
    op.drop_column("cameras", "vlm_refiner_max_tokens")
    op.drop_column("cameras", "vlm_refiner_keywords")
    op.drop_column("cameras", "vlm_refiner_trigger_objects")
    op.drop_column("cameras", "vlm_refiner_provider_id")
