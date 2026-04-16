"""add multi-model detection config

Revision ID: a1b2c3d4e5f6
Revises: d49e5aa857bd
Create Date: 2026-04-16 15:22:31.493817
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'd49e5aa857bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cameras', sa.Column('detection_models', sa.JSON(), nullable=True))
    op.add_column('cameras', sa.Column('detection_merge', sa.String(length=16), nullable=False, server_default='any'))
    op.add_column('cameras', sa.Column('detection_consensus_min', sa.Integer(), nullable=False, server_default='2'))


def downgrade() -> None:
    op.drop_column('cameras', 'detection_consensus_min')
    op.drop_column('cameras', 'detection_merge')
    op.drop_column('cameras', 'detection_models')
