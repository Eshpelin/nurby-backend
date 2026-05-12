"""add body re-identification tables

Revision ID. c6d7e8f9a3b5
Revises. b5c6d7e8f9a2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "c6d7e8f9a3b5"
down_revision: Union[str, None] = "b5c6d7e8f9a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "body_clusters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("representative_embedding", Vector(512), nullable=False),
        sa.Column("representative_color", sa.JSON(), nullable=True),
        sa.Column("sample_thumbnail_path", sa.String(length=1024), nullable=True),
        sa.Column("sighting_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("first_camera_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Linked Person. Either set directly when user names this cluster, or
        # inherited via face cluster merge once a face hit confirms identity.
        sa.Column("person_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("persons.id", ondelete="SET NULL"), nullable=True, index=True),
        # Optional. Once a face cluster has been linked to the same Person,
        # this points at it for fast cross-modal queries.
        sa.Column("linked_face_cluster_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("face_clusters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("confidence", sa.String(length=16), nullable=False, server_default="tentative"),
        sa.Column("auto_label_number", sa.Integer(), nullable=True, unique=True),
        sa.Column("appearance_description", sa.Text(), nullable=True),
    )

    op.create_table(
        "body_cluster_samples",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("body_clusters.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("camera_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("embedding", Vector(512), nullable=False),
        sa.Column("color_histogram", sa.JSON(), nullable=True),
        sa.Column("thumbnail_path", sa.String(length=1024), nullable=True),
        sa.Column("bbox", sa.JSON(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # IVFFlat vector index for sub-linear nearest-neighbor lookup. Required
    # for live clustering once the table grows past a few thousand rows.
    op.execute(
        "CREATE INDEX ix_body_clusters_repr_ivfflat ON body_clusters "
        "USING ivfflat (representative_embedding vector_cosine_ops) WITH (lists = 50)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_body_clusters_repr_ivfflat")
    op.drop_table("body_cluster_samples")
    op.drop_table("body_clusters")
