"""add incidents table + camera config + observation link

Revision ID: f2a3b4c5d6e8
Revises: e1f2a3b4c5d7
Create Date: 2026-05-08 09:00:00.000000

Server-side incident tracking. An incident is a rolling artifact
that groups related observations on a single camera by identity
signature (named person, face cluster, top object set) within an
idle window. Replaces the dashboard's frontend-only coalescing with
persistent rows that have stable ids, WS append events, and a
final VLM summary on close.

Default behavior. on for every camera, 10 min idle window. Set
``incident_tracking_enabled=false`` per camera to fall back to the
frontend coalescer for that feed.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "f2a3b4c5d6e8"
down_revision: Union[str, None] = "e1f2a3b4c5d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "incidents",
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
        # Signature kind tells the UI which icon / phrasing to use.
        # Values. ``person`` (named match), ``cluster`` (recurring
        # unknown), ``unknown`` (one-off unknown face), ``object``
        # (top YOLO labels), ``motion`` (no face / no object).
        sa.Column("signature_kind", sa.String(16), nullable=False),
        sa.Column("signature_key", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "finalized",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "occurrence_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "peak_observation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("observations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("observation_ids", postgresql.JSON(), nullable=True),
        sa.Column("thumbnails", postgresql.JSON(), nullable=True),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("summary_provider_name", sa.String(64), nullable=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_incidents_camera_started",
        "incidents",
        ["camera_id", sa.text("started_at DESC")],
    )
    # The finalizer scans 'open incidents' often. Partial-style index
    # on (camera, finalized, last_seen_at) keeps that lookup tight.
    op.create_index(
        "ix_incidents_open",
        "incidents",
        ["camera_id", "finalized", "last_seen_at"],
    )

    op.add_column(
        "observations",
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    op.add_column(
        "cameras",
        sa.Column(
            "incident_tracking_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "cameras",
        sa.Column(
            "incident_idle_seconds",
            sa.Integer(),
            nullable=False,
            server_default="600",
        ),
    )


def downgrade() -> None:
    op.drop_column("cameras", "incident_idle_seconds")
    op.drop_column("cameras", "incident_tracking_enabled")
    op.drop_column("observations", "incident_id")
    op.drop_index("ix_incidents_open", table_name="incidents")
    op.drop_index("ix_incidents_camera_started", table_name="incidents")
    op.drop_table("incidents")
