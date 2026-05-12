"""add journeys table + incident link + camera config

Revision ID: b4c5d6e7f8a1
Revises: a3b4c5d6e7f9
Create Date: 2026-05-08 16:00:00.000000

Cross-camera story tracking. A journey groups incidents for the
same subject (named person OR face cluster) across multiple cameras
within an idle window. Renders as a single timeline card.

   Gate Cam (22:14) → Garage (22:16) → Back Door (22:18)

Incidents stay per-camera, journeys stitch them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "b4c5d6e7f8a1"
down_revision: Union[str, None] = "a3b4c5d6e7f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "journeys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Subject. one journey per subject at a time. Subject kind
        # mirrors incident.signature_kind values but only ``person``
        # and ``cluster`` make sense at journey scope (motion / object
        # signatures are not stable across cameras).
        sa.Column("subject_kind", sa.String(16), nullable=False),
        sa.Column("subject_key", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "finalized",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # JSON list of {camera_id, camera_name, incident_id,
        # started_at, last_seen_at, occurrence_count}. Time-ordered.
        sa.Column("segments", postgresql.JSON(), nullable=False),
        # JSON list of {from_camera_id, to_camera_id, gap_seconds}.
        # Computed from successive segments on different cameras.
        sa.Column("transitions", postgresql.JSON(), nullable=True),
        sa.Column("cameras_seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("incidents_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("summary_provider_name", sa.String(64), nullable=True),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_journeys_started",
        "journeys",
        [sa.text("started_at DESC")],
    )
    # Worker query target. open journeys for a given subject within
    # the idle window.
    op.create_index(
        "ix_journeys_open_subject",
        "journeys",
        ["subject_kind", "subject_key", "finalized", "last_seen_at"],
    )

    op.add_column(
        "incidents",
        sa.Column(
            "journey_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("journeys.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("incidents", "journey_id")
    op.drop_index("ix_journeys_open_subject", table_name="journeys")
    op.drop_index("ix_journeys_started", table_name="journeys")
    op.drop_table("journeys")
