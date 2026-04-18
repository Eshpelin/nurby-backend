"""add cluster auto label and appearance

Revision ID: e7a1b2c3d4e5
Revises: 5ac1155eecd0
Create Date: 2026-04-18 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'e7a1b2c3d4e5'
down_revision: Union[str, None] = '5ac1155eecd0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Sequential human label for unknown clusters. "Unknown 645" etc.
    op.execute("CREATE SEQUENCE IF NOT EXISTS face_cluster_label_seq START 1")

    op.add_column(
        "face_clusters",
        sa.Column("auto_label_number", sa.Integer(), nullable=True),
    )
    op.add_column(
        "face_clusters",
        sa.Column("appearance_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "face_clusters",
        sa.Column(
            "appearance_description_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
    )

    op.create_index(
        "ix_face_clusters_auto_label_number",
        "face_clusters",
        ["auto_label_number"],
        unique=True,
    )

    # Backfill existing rows with sequential numbers
    op.execute(
        "UPDATE face_clusters "
        "SET auto_label_number = nextval('face_cluster_label_seq') "
        "WHERE auto_label_number IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_face_clusters_auto_label_number", table_name="face_clusters")
    op.drop_column("face_clusters", "appearance_description_status")
    op.drop_column("face_clusters", "appearance_description")
    op.drop_column("face_clusters", "auto_label_number")
    op.execute("DROP SEQUENCE IF EXISTS face_cluster_label_seq")
