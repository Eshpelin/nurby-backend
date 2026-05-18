"""add ack + mute/snooze fields for telegram phase 2

Revision ID. e2a3b4c5d7e8
Revises. d7e1f2a3b4c6

Adds Phase 2 ack + suppression columns. ``events.acked_at`` is the new
authoritative ack timestamp set by either the web UI or the Telegram
button. The pre-existing ``events.acknowledged_at`` column from Phase 1
is left in place for backwards compat but new code reads/writes the
``acked_*`` triad. ``events.muted_until`` lets a single event silence
follow-on Telegram messages until the timestamp passes;
``rules.snoozed_until`` is the rule-level counterpart wired by the
"Snooze rule 1 hour" button.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e2a3b4c5d7e8"
down_revision: Union[str, None] = "d7e1f2a3b4c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column(
            "acked_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "events",
        sa.Column("acked_via", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "rules",
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rules", "snoozed_until")
    op.drop_column("events", "muted_until")
    op.drop_column("events", "acked_via")
    op.drop_column("events", "acked_by_user_id")
    op.drop_column("events", "acked_at")
