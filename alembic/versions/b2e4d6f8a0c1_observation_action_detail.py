"""open-world detail on observation_actions

Revision ID: b2e4d6f8a0c1
Revises: a1f3c5e7b9d2
Create Date: 2026-06-08 13:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b2e4d6f8a0c1'
down_revision: Union[str, None] = 'a1f3c5e7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "observation_actions",
        sa.Column("detail", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("observation_actions", "detail")
