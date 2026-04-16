"""add digest entries table

Revision ID: a2b3c4d5e6f7
Revises: f5a6b7c8d9e0
Create Date: 2026-04-16 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, None] = 'f5a6b7c8d9e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('digest_entries',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('camera_id', sa.UUID(), nullable=True),
        sa.Column('period', sa.String(length=10), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('highlights', sa.JSON(), nullable=True),
        sa.Column('stats', sa.JSON(), nullable=True),
        sa.Column('total_observations', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['camera_id'], ['cameras.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_digest_entries_camera_id'), 'digest_entries', ['camera_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_digest_entries_camera_id'), table_name='digest_entries')
    op.drop_table('digest_entries')
