"""audio transcription on by default

Revision ID: c3e5a7b9d1f4
Revises: b2d4f6a8c1e3
Create Date: 2026-06-04 18:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c3e5a7b9d1f4'
down_revision: Union[str, None] = 'b2d4f6a8c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New cameras transcribe by default.
    op.alter_column(
        "cameras",
        "audio_transcribe_enabled",
        server_default=sa.true(),
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
    # Turn it on for existing cameras too.
    op.execute("UPDATE cameras SET audio_transcribe_enabled = true")


def downgrade() -> None:
    op.alter_column(
        "cameras",
        "audio_transcribe_enabled",
        server_default=sa.false(),
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
