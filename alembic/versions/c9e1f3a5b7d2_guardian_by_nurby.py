"""guardian by nurby: facilities, guardian links, approved pickups, access log

Revision ID: c9e1f3a5b7d2
Revises: b8d0f2a4c6e9
Create Date: 2026-06-07 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON, UUID


revision: str = 'c9e1f3a5b7d2'
down_revision: Union[str, None] = 'b8d0f2a4c6e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "facilities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reveal_min_confidence", sa.Float(), nullable=True),
        sa.Column("max_cameras_per_person", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_facilities_slug", "facilities", ["slug"], unique=True)

    op.create_table(
        "guardian_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("facility_id", UUID(as_uuid=True), sa.ForeignKey("facilities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("guardian_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship_label", sa.String(64), nullable=True),
        sa.Column("tier", sa.String(16), nullable=False, server_default="full"),
        sa.Column("alert_prefs", JSON(), nullable=True),
        sa.Column("premium", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("live_presence", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("live_video", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("audio", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_primary_parent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reveal_min_confidence", sa.Float(), nullable=True),
        sa.Column("granted_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_image_served_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("guardian_user_id", "person_id", name="uq_guardian_person"),
    )
    op.create_index("ix_guardian_links_facility_id", "guardian_links", ["facility_id"])
    op.create_index("ix_guardian_links_person_id", "guardian_links", ["person_id"])
    op.create_index("ix_guardian_links_guardian_user_id", "guardian_links", ["guardian_user_id"])

    op.create_table(
        "approved_pickups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("person_id", UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="person"),
        sa.Column("linked_person_id", UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="SET NULL"), nullable=True),
        sa.Column("vehicle_plate", sa.String(32), nullable=True),
        sa.Column("photo_path", sa.String(1024), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_approved_pickups_person_id", "approved_pickups", ["person_id"])
    op.create_index("ix_approved_pickups_linked_person_id", "approved_pickups", ["linked_person_id"])

    op.create_table(
        "guardian_access_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("guardian_link_id", UUID(as_uuid=True), sa.ForeignKey("guardian_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("guardian_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("detail", JSON(), nullable=True),
    )
    op.create_index("ix_guardian_access_log_link", "guardian_access_log", ["guardian_link_id"])
    op.create_index("ix_guardian_access_log_user", "guardian_access_log", ["guardian_user_id"])
    op.create_index("ix_guardian_access_log_person", "guardian_access_log", ["person_id"])
    op.create_index("ix_guardian_access_log_at", "guardian_access_log", ["at"])


def downgrade() -> None:
    op.drop_table("guardian_access_log")
    op.drop_table("approved_pickups")
    op.drop_table("guardian_links")
    op.drop_index("ix_facilities_slug", table_name="facilities")
    op.drop_table("facilities")
