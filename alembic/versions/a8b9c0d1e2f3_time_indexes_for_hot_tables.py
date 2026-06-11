"""btree time indexes for the hot list queries

Revision ID: a8b9c0d1e2f3
Revises: d9e0f1a2b3c4
Create Date: 2026-06-11 00:00:00.000000

Every timeline surface (dashboard activity, observations list, recordings
library, events feed, guardian presence) orders by a time column and limits.
None of those columns had a btree index, so each request sorted the full
table. The existing GiST index on observations covers range-overlap joins
only, not ORDER BY.

- observations: (camera_id, started_at) composite for per-camera timelines
  plus a plain started_at index for the global feed.
- recordings: started_at.
- events: fired_at.

CONCURRENTLY is deliberately not used: these run inside alembic's
transaction on startup, and the tables on a self-hosted box are small
enough for a brief lock.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_observations_started_at", "observations", ["started_at"],
        unique=False, if_not_exists=True,
    )
    op.create_index(
        "ix_observations_camera_started", "observations",
        ["camera_id", "started_at"], unique=False, if_not_exists=True,
    )
    op.create_index(
        "ix_recordings_started_at", "recordings", ["started_at"],
        unique=False, if_not_exists=True,
    )
    op.create_index(
        "ix_events_fired_at", "events", ["fired_at"],
        unique=False, if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_events_fired_at", table_name="events", if_exists=True)
    op.drop_index("ix_recordings_started_at", table_name="recordings", if_exists=True)
    op.drop_index(
        "ix_observations_camera_started", table_name="observations", if_exists=True
    )
    op.drop_index(
        "ix_observations_started_at", table_name="observations", if_exists=True
    )
