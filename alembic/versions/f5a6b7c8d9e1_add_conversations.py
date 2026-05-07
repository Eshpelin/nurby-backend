"""add conversations table + camera config + transcript link

Revision ID: f5a6b7c8d9e1
Revises: e4f5a6b7c8d9
Create Date: 2026-05-07 14:00:00.000000

A conversation groups consecutive transcripts on a camera into a
single rolling artifact. Boundary is a gap heuristic. when the time
between the previous transcript's end and the next transcript's start
exceeds ``conversation_gap_seconds``, the next transcript opens a new
conversation.

The summarizer worker finalizes a conversation after it has been
quiet for the gap window. On finalize we call a VLM over the full
text and store the summary so the timeline can collapse a 30s
back-and-forth into a single card with a one-paragraph recap instead
of N transcript cards.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "f5a6b7c8d9e1"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversations",
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        # ended_at_provisional advances every time a transcript is
        # appended. ended_at is set when the conversation is finalized.
        sa.Column(
            "ended_at_provisional",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transcript_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("finalized", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("summary_provider_name", sa.String(64), nullable=True),
        sa.Column("speakers_seen", postgresql.JSON(), nullable=True),
        # 384-dim to match observations + summaries embeddings.
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_conversations_camera_started",
        "conversations",
        ["camera_id", sa.text("started_at DESC")],
    )
    # Worker queries "non-finalized conversations to maybe finalize".
    op.create_index(
        "ix_conversations_open",
        "conversations",
        ["camera_id", "finalized", "ended_at_provisional"],
    )

    # Transcripts gain a conversation_id link, nullable for backfill /
    # filtered rows.
    op.add_column(
        "transcripts",
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    # Per-camera config.
    op.add_column(
        "cameras",
        sa.Column(
            "conversation_gap_seconds",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )
    op.add_column(
        "cameras",
        sa.Column(
            "conversation_summary_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "cameras",
        sa.Column(
            "conversation_min_messages_for_summary",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
    )


def downgrade() -> None:
    op.drop_column("cameras", "conversation_min_messages_for_summary")
    op.drop_column("cameras", "conversation_summary_enabled")
    op.drop_column("cameras", "conversation_gap_seconds")
    op.drop_column("transcripts", "conversation_id")
    op.drop_index("ix_conversations_open", table_name="conversations")
    op.drop_index("ix_conversations_camera_started", table_name="conversations")
    op.drop_table("conversations")
