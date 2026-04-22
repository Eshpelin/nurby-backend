"""add person starred + recap fields

Revision ID: a8c9d0e1f2a3
Revises: e7a1b2c3d4e5
Create Date: 2026-04-22 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a8c9d0e1f2a3'
down_revision: Union[str, None] = 'e7a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "persons",
        sa.Column("is_starred", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("persons", sa.Column("recap_prompt", sa.Text(), nullable=True))
    op.add_column("persons", sa.Column("recap_provider", sa.String(length=32), nullable=True))
    op.add_column("persons", sa.Column("recap_model", sa.String(length=255), nullable=True))
    op.add_column("persons", sa.Column("recap_cached_status", sa.Text(), nullable=True))
    op.add_column(
        "persons",
        sa.Column("recap_cached_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "persons",
        sa.Column("recap_stale", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_persons_is_starred",
        "persons",
        ["is_starred"],
        postgresql_where=sa.text("is_starred = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_persons_is_starred", table_name="persons")
    op.drop_column("persons", "recap_stale")
    op.drop_column("persons", "recap_cached_at")
    op.drop_column("persons", "recap_cached_status")
    op.drop_column("persons", "recap_model")
    op.drop_column("persons", "recap_provider")
    op.drop_column("persons", "recap_prompt")
    op.drop_column("persons", "is_starred")
