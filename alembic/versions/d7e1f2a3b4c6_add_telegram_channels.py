"""add telegram_channels

Revision ID. d7e1f2a3b4c6
Revises. c6d7e8f9a3b5
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d7e1f2a3b4c6"
down_revision: Union[str, None] = "c6d7e8f9a3b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("label", sa.String(length=64), nullable=False),
        # Fernet-encrypted Telegram bot token. Never returned to clients.
        sa.Column("bot_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("bot_username", sa.String(length=64), nullable=True),
        # chat_id is null until the pairing flow completes (user hits /start
        # in the target chat with a one-shot nonce).
        sa.Column("chat_id", sa.String(length=64), nullable=True),
        sa.Column("chat_title", sa.String(length=255), nullable=True),
        sa.Column("chat_type", sa.String(length=16), nullable=True),
        sa.Column("default_silent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("telegram_channels")
