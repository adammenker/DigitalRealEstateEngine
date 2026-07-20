"""add competitor SERP provenance

Revision ID: e9a4b2c6d8f1
Revises: d8f3a7c1e5b9
Create Date: 2026-07-19 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e9a4b2c6d8f1"
down_revision = "d8f3a7c1e5b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("competitor_metrics") as batch:
        batch.add_column(sa.Column("representative_query", sa.Text(), nullable=True))
        batch.add_column(sa.Column("serp_position", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "serp_observations",
                sa.JSON(),
                server_default=sa.text("'[]'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("competitor_metrics") as batch:
        batch.drop_column("serp_observations")
        batch.drop_column("serp_position")
        batch.drop_column("representative_query")
