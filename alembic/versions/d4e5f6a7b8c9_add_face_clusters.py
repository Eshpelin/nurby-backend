"""add face clusters

Revision ID: d4e5f6a7b8c9
Revises: 936b67ae5404
Create Date: 2026-04-16 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = '936b67ae5404'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'face_clusters',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('representative_embedding', Vector(128), nullable=False),
        sa.Column('sample_thumbnail_path', sa.String(1024), nullable=True),
        sa.Column('sighting_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('first_camera_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('person_id', sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey('persons.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
    )
    op.create_table(
        'face_cluster_samples',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('cluster_id', sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey('face_clusters.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('camera_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('embedding', Vector(128), nullable=False),
        sa.Column('thumbnail_path', sa.String(1024), nullable=True),
        sa.Column('captured_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('face_cluster_samples')
    op.drop_table('face_clusters')
