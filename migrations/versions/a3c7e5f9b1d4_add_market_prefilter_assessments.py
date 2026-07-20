"""add market prefilter assessments

Revision ID: a3c7e5f9b1d4
Revises: e9a4b2c6d8f1
Create Date: 2026-07-19 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3c7e5f9b1d4"
down_revision = "e9a4b2c6d8f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_prefilter_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_text", sa.String(length=240), nullable=False),
        sa.Column("service_profile", sa.String(length=80), nullable=False),
        sa.Column("geography_kind", sa.String(length=40), nullable=False),
        sa.Column("state_filters", sa.JSON(), nullable=False),
        sa.Column("minimum_population", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("returned_count", sa.Integer(), nullable=False),
        sa.Column("assessment_version", sa.String(length=40), nullable=False),
        sa.Column("config_hash", sa.String(length=40), nullable=False),
        sa.Column("geography_dataset_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_table(
        "market_prefilter_assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "prefilter_run_id",
            sa.Integer(),
            sa.ForeignKey("market_prefilter_runs.id"),
            nullable=False,
        ),
        sa.Column("geography_id", sa.String(length=80), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("recommendation", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint(
            "prefilter_run_id",
            "geography_id",
            name="uq_market_prefilter_run_geography",
        ),
    )
    op.create_index(
        "ix_market_prefilter_assessments_geography_id",
        "market_prefilter_assessments",
        ["geography_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_market_prefilter_assessments_geography_id",
        table_name="market_prefilter_assessments",
    )
    op.drop_table("market_prefilter_assessments")
    op.drop_table("market_prefilter_runs")
