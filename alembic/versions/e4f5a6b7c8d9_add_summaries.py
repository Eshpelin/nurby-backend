"""add summaries table + camera summary config

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-05-07 12:00:00.000000

Phase 1 of camera summarization. Introduces the ``summaries`` table
that holds VLM-generated narrative recaps over a time window, plus
per-camera config columns that drive the summarizer worker.

Two summarization modes are supported.
- ``periodic`` summarizes every ``summary_period_seconds``.
- ``event`` opens an event when a camera sees a label in
  ``summary_event_trigger_objects`` and closes it after
  ``summary_event_quiet_seconds`` of no matching activity. The closed
  event is then summarized.

Defaults bias toward "person" so a fresh camera generates useful
summaries without configuration. Per-camera override is required for
pet-cams, wildlife-cams, etc.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New camera columns.
    op.add_column(
        "cameras",
        sa.Column(
            "summary_provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "cameras",
        sa.Column(
            "summary_mode",
            sa.String(16),
            nullable=False,
            server_default="off",
        ),
    )
    op.add_column(
        "cameras",
        sa.Column(
            "summary_period_seconds",
            sa.Integer(),
            nullable=False,
            server_default="1800",
        ),
    )
    op.add_column(
        "cameras",
        sa.Column(
            "summary_event_quiet_seconds",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
    )
    # JSON list of YOLO labels that count as activity for event mode.
    # Default ["person"]. User overrides for pet-cams etc.
    op.add_column(
        "cameras",
        sa.Column(
            "summary_event_trigger_objects",
            postgresql.JSON(),
            nullable=True,
        ),
    )
    # Backfill existing rows with the default trigger.
    op.execute(
        """
        UPDATE cameras
        SET summary_event_trigger_objects = '["person"]'::json
        WHERE summary_event_trigger_objects IS NULL
        """
    )
    op.add_column(
        "cameras",
        sa.Column(
            "summary_event_min_duration_seconds",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )
    op.add_column(
        "cameras",
        sa.Column(
            "summary_max_tokens",
            sa.Integer(),
            nullable=False,
            server_default="400",
        ),
    )

    # Summaries table.
    op.create_table(
        "summaries",
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
        sa.Column("kind", sa.String(16), nullable=False),  # periodic | event
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=True),
        sa.Column("trigger_reason", sa.String(32), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("source_observation_ids", postgresql.JSON(), nullable=True),
        sa.Column("source_transcript_ids", postgresql.JSON(), nullable=True),
        sa.Column("people_seen", postgresql.JSON(), nullable=True),
        sa.Column("plates_seen", postgresql.JSON(), nullable=True),
        sa.Column("object_counts", postgresql.JSON(), nullable=True),
        # 384-dim embedding to match observations.description_embedding so
        # union search (observations + transcripts + summaries) stays
        # one-shot.
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_summaries_camera_started",
        "summaries",
        ["camera_id", sa.text("started_at DESC")],
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_summaries_embedding "
        "ON summaries USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_summaries_embedding")
    op.drop_index("ix_summaries_camera_started", table_name="summaries")
    op.drop_table("summaries")

    op.drop_column("cameras", "summary_max_tokens")
    op.drop_column("cameras", "summary_event_min_duration_seconds")
    op.drop_column("cameras", "summary_event_trigger_objects")
    op.drop_column("cameras", "summary_event_quiet_seconds")
    op.drop_column("cameras", "summary_period_seconds")
    op.drop_column("cameras", "summary_mode")
    op.drop_column("cameras", "summary_provider_id")
