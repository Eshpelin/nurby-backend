"""add vlm_late + vlm_enqueued_at to observations

Revision ID. d9e0f1a2b3c4
Revises. c8d9e0f1a2b3
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("observations", sa.Column("vlm_late", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("observations", sa.Column("vlm_enqueued_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("observations", "vlm_enqueued_at")
    op.drop_column("observations", "vlm_late")
