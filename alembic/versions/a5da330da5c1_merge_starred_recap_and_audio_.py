"""merge starred recap and audio detections heads

Revision ID: a5da330da5c1
Revises: a8c9d0e1f2a3, b1c2d3e4f5a6
Create Date: 2026-04-22 17:34:42.384733
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a5da330da5c1'
down_revision: Union[str, None] = ('a8c9d0e1f2a3', 'b1c2d3e4f5a6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
