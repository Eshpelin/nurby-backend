"""observations time range gist index for audio overlap joins

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-04-24 00:00:00.000000

Phase 0 of audio transcription plan. Prepares the `observations` table for
cheap range-overlap joins against the forthcoming `transcripts` table.

Composite index on (camera_id, tstzrange(started_at, ended_at)) using GiST.
Requires the `btree_gist` extension because uuid is not natively GiST-able.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS observations_time_range_idx
        ON observations
        USING gist (camera_id, tstzrange(started_at, ended_at))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS observations_time_range_idx")
    # btree_gist may be used by other future indexes. Leave it installed.
