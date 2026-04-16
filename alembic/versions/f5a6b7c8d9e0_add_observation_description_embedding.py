"""add observation description embedding

Revision ID: f5a6b7c8d9e0
Revises: e1f2a3b4c5d6
Create Date: 2026-04-16 23:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector


revision: str = 'f5a6b7c8d9e0'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'observations',
        sa.Column('description_embedding', Vector(384), nullable=True),
    )

    # The ivfflat index requires rows to exist before creation.
    # Using IF NOT EXISTS so this is safe to re-run.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_observations_description_embedding "
        "ON observations USING ivfflat (description_embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.drop_index('ix_observations_description_embedding', table_name='observations')
    op.drop_column('observations', 'description_embedding')
