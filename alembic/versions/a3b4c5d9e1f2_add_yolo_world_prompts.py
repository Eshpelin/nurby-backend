"""add cameras.yolo_world_prompts for open-vocab detection

Revision ID: a3b4c5d9e1f2
Revises: f8a3b4c5d9e1
Create Date: 2026-05-08 23:00:00.000000

YOLO-World v2 takes a list of class names in plain English and
detects those exact classes. Each camera carries its own prompt
list so Front Door can detect 'person, package, delivery driver,
mail truck' while Kitchen detects 'person, cat, knife, fire'. The
pipeline passes the list to the model on every inference.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a3b4c5d9e1f2"
down_revision: Union[str, None] = "f8a3b4c5d9e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column("yolo_world_prompts", postgresql.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cameras", "yolo_world_prompts")
