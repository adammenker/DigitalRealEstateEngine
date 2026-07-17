"""baseline schema

Revision ID: 35a8b4f9eb77
Revises:
Create Date: 2026-07-12 23:27:25.097841
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.types import JSON


revision = "35a8b4f9eb77"
down_revision = None
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "service_families",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("seed_queries", JSON(), nullable=False),
        sa.Column("negative_terms", JSON(), nullable=False),
        sa.Column("provider_categories", JSON(), nullable=False),
        sa.Column("regulated", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
    )
    op.create_index(
        "ix_service_families_slug",
        "service_families",
        ["slug"],
        unique=True,
    )

    op.create_table(
        "markets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=True),
        sa.Column("cities", JSON(), nullable=False),
        sa.Column("postal_codes", JSON(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("provider_location_code", sa.String(length=80), nullable=True),
        sa.Column("provider_location_name", sa.String(length=200), nullable=True),
        sa.Column("resolution_metadata", JSON(), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_markets_slug", "markets", ["slug"], unique=True)

    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_family_id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("latest_score", sa.Float(), nullable=True),
        sa.Column("score_version", sa.String(length=40), nullable=True),
        sa.Column("confidence", sa.String(length=20), nullable=True),
        sa.Column("missing_data_flags", JSON(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.ForeignKeyConstraint(["service_family_id"], ["service_families.id"]),
        sa.UniqueConstraint(
            "service_family_id",
            "market_id",
            name="uq_opportunity_service_market",
        ),
    )

    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False),
        sa.Column("actual_cost_usd", sa.Float(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("integration_versions", JSON(), nullable=False),
        sa.Column("request_parameters", JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
    )

    op.create_table(
        "raw_api_responses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cache_key", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("endpoint", sa.String(length=120), nullable=False),
        sa.Column("parameters", JSON(), nullable=False),
        sa.Column("api_version", sa.String(length=40), nullable=False),
        sa.Column("response_json", JSON(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("request_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("provider_task_id", sa.String(length=120), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "ix_raw_api_responses_cache_key",
        "raw_api_responses",
        ["cache_key"],
        unique=True,
    )

    op.create_table(
        "json_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("payload", JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
    )
    op.create_index("ix_json_artifacts_kind", "json_artifacts", ["kind"])

    op.create_table(
        "provider_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("provider_candidate_id", sa.Integer(), nullable=True),
        sa.Column("routing_notes", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
    )

    op.create_table(
        "intervention_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("lifecycle_stage", sa.String(length=80), nullable=False),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("recurs_for_every_property", sa.Boolean(), nullable=False),
        sa.Column("suggested_future_automation", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
    )


def downgrade() -> None:
    op.drop_table("intervention_logs")
    op.drop_table("provider_configs")
    op.drop_index("ix_json_artifacts_kind", table_name="json_artifacts")
    op.drop_table("json_artifacts")
    op.drop_index("ix_raw_api_responses_cache_key", table_name="raw_api_responses")
    op.drop_table("raw_api_responses")
    op.drop_table("scan_runs")
    op.drop_table("opportunities")
    op.drop_index("ix_markets_slug", table_name="markets")
    op.drop_table("markets")
    op.drop_index("ix_service_families_slug", table_name="service_families")
    op.drop_table("service_families")
