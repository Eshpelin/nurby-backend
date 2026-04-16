"""add VLM trigger config

Revision ID: b7e8f9a0c1d2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-16 16:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b7e8f9a0c1d2'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cameras', sa.Column('vlm_trigger', sa.String(length=16), nullable=False, server_default='always'))
    op.add_column('cameras', sa.Column('vlm_trigger_objects', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('cameras', 'vlm_trigger_objects')
    op.drop_column('cameras', 'vlm_trigger')
