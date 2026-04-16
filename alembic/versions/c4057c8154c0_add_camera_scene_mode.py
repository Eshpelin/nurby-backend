"""add camera scene_mode

Revision ID: c4057c8154c0
Revises: 5f395ac9f751
Create Date: 2026-04-16 20:01:03.246528
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c4057c8154c0'
down_revision: Union[str, None] = '5f395ac9f751'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cameras', sa.Column('scene_mode', sa.String(length=16), server_default='indoor', nullable=False))


def downgrade() -> None:
    op.drop_column('cameras', 'scene_mode')
