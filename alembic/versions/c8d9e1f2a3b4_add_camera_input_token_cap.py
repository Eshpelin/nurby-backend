"""add cameras.vlm_max_input_tokens override

Revision ID: c8d9e1f2a3b4
Revises: b7c8d9e1f2a3
Create Date: 2026-05-07 18:30:00.000000

Per-camera tighten of the provider input cap. NULL means defer to
the provider cap. Cameras already had vlm_max_tokens (output
override). This adds the input-side equivalent so a camera can
trim its own prompt without changing the shared provider config.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c8d9e1f2a3b4"
down_revision: Union[str, None] = "b7c8d9e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column("vlm_max_input_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cameras", "vlm_max_input_tokens")
