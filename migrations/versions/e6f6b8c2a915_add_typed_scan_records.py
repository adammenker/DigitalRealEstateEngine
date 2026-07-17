"""add typed scan records

Revision ID: e6f6b8c2a915
Revises: 9d2c1e8b7a44
Create Date: 2026-07-13 00:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.types import JSON


revision = "e6f6b8c2a915"
down_revision = "9d2c1e8b7a44"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def upgrade() -> None:
    op.create_table(
        "scan_plan_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("endpoint", sa.String(length=160), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("request_parameters", JSON(), nullable=False),
        sa.Column("cache_key", sa.String(length=128), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("request_known", sa.Boolean(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
    )
    op.create_index("ix_scan_plan_calls_cache_key", "scan_plan_calls", ["cache_key"])

    op.create_table(
        "keyword_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("keyword", sa.String(length=240), nullable=False),
        sa.Column("canonical_keyword", sa.String(length=240), nullable=False),
        sa.Column("intent", sa.String(length=80), nullable=False),
        sa.Column("search_volume", sa.Integer(), nullable=True),
        sa.Column("cpc", sa.Float(), nullable=True),
        sa.Column("paid_competition", sa.Float(), nullable=True),
        sa.Column("monthly_history", JSON(), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market_granularity", sa.String(length=40), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.Column("excluded_reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
    )

    op.create_table(
        "serp_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("query", sa.String(length=240), nullable=False),
        sa.Column("market_id", sa.String(length=160), nullable=False),
        sa.Column("device", sa.String(length=40), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("features_present", JSON(), nullable=False),
        sa.Column("raw_response_ref", sa.String(length=160), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
    )

    op.create_table(
        "serp_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("serp_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("result_type", sa.String(length=80), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=240), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("classification", sa.String(length=80), nullable=False),
        sa.Column("is_local_provider", sa.Boolean(), nullable=False),
        sa.Column("is_directory", sa.Boolean(), nullable=False),
        sa.Column("is_national_brand", sa.Boolean(), nullable=False),
        sa.Column("is_lead_generation_site", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["serp_snapshot_id"], ["serp_snapshots.id"]),
    )

    op.create_table(
        "competitor_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=240), nullable=False),
        sa.Column("referring_domains", sa.Integer(), nullable=True),
        sa.Column("backlinks", sa.Integer(), nullable=True),
        sa.Column("authority", sa.Float(), nullable=True),
        sa.Column("page_relevance_score", sa.Float(), nullable=True),
        sa.Column("local_relevance", sa.Float(), nullable=True),
        sa.Column("page_type", sa.String(length=80), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
    )

    op.create_table(
        "provider_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(length=80), nullable=True),
        sa.Column("email", sa.String(length=240), nullable=True),
        sa.Column("contact_form_url", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("service_area", sa.String(length=200), nullable=True),
        sa.Column("category", sa.String(length=160), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("review_count", sa.Integer(), nullable=True),
        sa.Column("business_status", sa.String(length=80), nullable=False),
        sa.Column("contact_confidence", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("raw_response_ref", sa.String(length=160), nullable=True),
        sa.Column("outreach_status", sa.String(length=80), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
    )


def downgrade() -> None:
    op.drop_table("provider_candidates")
    op.drop_table("competitor_metrics")
    op.drop_table("serp_results")
    op.drop_table("serp_snapshots")
    op.drop_table("keyword_metrics")
    op.drop_index("ix_scan_plan_calls_cache_key", table_name="scan_plan_calls")
    op.drop_table("scan_plan_calls")
