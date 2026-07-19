"""add provider suitability evidence

Revision ID: f2a6c9d4e8b1
Revises: d7f4a9c8b2e1
Create Date: 2026-07-18 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.types import JSON


revision = "f2a6c9d4e8b1"
down_revision = "d7f4a9c8b2e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("provider_candidates") as batch:
        batch.add_column(
            sa.Column("categories", JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("latitude", sa.Float(), nullable=True))
        batch.add_column(sa.Column("longitude", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column(
                "source_timestamp",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            )
        )
        batch.add_column(
            sa.Column("suitability_signals", JSON(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    with op.batch_alter_table("provider_candidates") as batch:
        batch.drop_column("suitability_signals")
        batch.drop_column("source_timestamp")
        batch.drop_column("longitude")
        batch.drop_column("latitude")
        batch.drop_column("categories")
