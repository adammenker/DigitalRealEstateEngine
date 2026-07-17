"""add service keyword config

Revision ID: b4e9a1c2d7f6
Revises: a1f7c3d9e8b2
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.types import JSON


revision = "b4e9a1c2d7f6"
down_revision = "a1f7c3d9e8b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("service_families") as batch:
        batch.add_column(sa.Column("intent_modifiers", JSON(), nullable=False, server_default="[]"))
        batch.add_column(
            sa.Column("negative_product_terms", JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("service_families") as batch:
        batch.drop_column("negative_product_terms")
        batch.drop_column("intent_modifiers")
