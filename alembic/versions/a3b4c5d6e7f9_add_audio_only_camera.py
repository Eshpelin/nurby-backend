"""add cameras.audio_only flag

Revision ID: a3b4c5d6e7f9
Revises: f2a3b4c5d6e8
Create Date: 2026-05-08 14:00:00.000000

Audio-only camera mode. Skips video decode + perception + recording
and runs only the audio pipeline (capture, VAD, STT, audio events,
clap pattern, speech phrase). The UI hides the video tile and shows
a mic indicator instead. Useful for rooms where a camera would be
inappropriate (bathroom, bedroom for baby-cry monitoring without
visual recording).

Flag, not a new stream_type. Stream type stays the transport.
audio_only is the behavior switch.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a3b4c5d6e7f9"
down_revision: Union[str, None] = "f2a3b4c5d6e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column(
            "audio_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("cameras", "audio_only")
