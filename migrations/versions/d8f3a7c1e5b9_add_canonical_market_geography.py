"""add canonical market geography

Revision ID: d8f3a7c1e5b9
Revises: c4b8e1f6a2d9
Create Date: 2026-07-19 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d8f3a7c1e5b9"
down_revision = "c4b8e1f6a2d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("markets") as batch:
        batch.add_column(sa.Column("county", sa.String(length=160), nullable=True))
        batch.add_column(sa.Column("county_fips", sa.String(length=5), nullable=True))
        batch.add_column(sa.Column("metro", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("metro_code", sa.String(length=5), nullable=True))
        batch.add_column(sa.Column("population", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reference_population", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("aliases", sa.JSON(), server_default=sa.text("'[]'"), nullable=False)
        )
        batch.add_column(sa.Column("boundary_radius_km", sa.Float(), nullable=True))
        batch.add_column(sa.Column("geography_id", sa.String(length=80), nullable=True))
        batch.add_column(
            sa.Column("geography_dataset_version", sa.String(length=80), nullable=True)
        )
        batch.create_index("ix_markets_geography_id", ["geography_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("markets") as batch:
        batch.drop_index("ix_markets_geography_id")
        batch.drop_column("geography_dataset_version")
        batch.drop_column("geography_id")
        batch.drop_column("boundary_radius_km")
        batch.drop_column("aliases")
        batch.drop_column("reference_population")
        batch.drop_column("population")
        batch.drop_column("metro_code")
        batch.drop_column("metro")
        batch.drop_column("county_fips")
        batch.drop_column("county")
