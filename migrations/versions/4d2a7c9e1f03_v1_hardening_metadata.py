"""v1 hardening metadata

Revision ID: 4d2a7c9e1f03
Revises: e6f6b8c2a915
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.types import JSON


revision = "4d2a7c9e1f03"
down_revision = "e6f6b8c2a915"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def upgrade() -> None:
    with op.batch_alter_table("scan_runs") as batch:
        batch.add_column(sa.Column("data_mode", sa.String(length=20), nullable=False, server_default="fixture"))
        batch.add_column(sa.Column("scan_profile", sa.String(length=40), nullable=False, server_default="testing"))
        batch.add_column(sa.Column("adapter_names", JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("adapter_versions", JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("normalization_version", sa.String(length=40), nullable=False, server_default="v1"))
        batch.add_column(sa.Column("scoring_version", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("cache_policy_version", sa.String(length=40), nullable=False, server_default="v2"))
        batch.add_column(sa.Column("planned_cost_usd", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("source_scan_run_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("progress_stage", sa.String(length=80), nullable=False, server_default="queued"))
        batch.add_column(sa.Column("partial_outputs", JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_foreign_key("fk_scan_runs_source_scan_run_id", "scan_runs", ["source_scan_run_id"], ["id"])

    with op.batch_alter_table("raw_api_responses") as batch:
        batch.add_column(sa.Column("response_shape_version", sa.String(length=40), nullable=False, server_default="v1"))
        batch.add_column(sa.Column("sanitized", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("provider_request_id", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("source_scan_run_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("checksum", sa.String(length=128), nullable=False, server_default=""))
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_raw_api_responses_source_scan_run_id", "scan_runs", ["source_scan_run_id"], ["id"])

    op.create_table(
        "api_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_run_id", sa.Integer(), nullable=True),
        sa.Column("raw_api_response_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("endpoint", sa.String(length=160), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("cache_key", sa.String(length=128), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("force_refresh", sa.Boolean(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False),
        sa.Column("actual_cost_usd", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["raw_api_response_id"], ["raw_api_responses.id"]),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
    )
    op.create_index("ix_api_calls_cache_key", "api_calls", ["cache_key"])

    op.create_table(
        "scan_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("scan_profile", sa.String(length=40), nullable=False),
        sa.Column("cache_hit_count", sa.Integer(), nullable=False),
        sa.Column("paid_call_count", sa.Integer(), nullable=False),
        sa.Column("estimated_uncached_cost_usd", sa.Float(), nullable=False),
        sa.Column("maximum_allowed_cost_usd", sa.Float(), nullable=False),
        sa.Column("confirmation_required", sa.Boolean(), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        sa.Column("block_reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
    )

    op.create_table(
        "keyword_clusters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("representative_keyword", sa.String(length=240), nullable=False),
        sa.Column("keywords", JSON(), nullable=False),
        sa.Column("dedupe_method", sa.String(length=80), nullable=False),
        sa.Column("combined_volume", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
    )
    op.create_table(
        "keyword_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.String(length=240), nullable=False),
        sa.Column("canonical_keyword", sa.String(length=240), nullable=False),
        sa.Column("decision", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("representative", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
    )
    op.create_table(
        "preliminary_assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("scoring_version", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("missing_components", JSON(), nullable=False),
        sa.Column("payload", JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
    )
    op.create_table(
        "full_opportunity_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("scoring_version", sa.String(length=40), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("payload", JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
    )
    op.create_table(
        "score_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("component", sa.String(length=120), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("inputs", JSON(), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("penalties", JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
    )
    op.create_table(
        "domain_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=240), nullable=False),
        sa.Column("availability_status", sa.String(length=80), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("payload", JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
    )
    op.create_table(
        "outreach_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("provider_candidate_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("payload", JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["provider_candidate_id"], ["provider_candidates.id"]),
    )
    op.create_table(
        "site_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("payload", JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("site_config_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("source_provider", sa.String(length=120), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("provenance", JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["site_config_id"], ["site_configs.id"]),
    )
    op.create_table(
        "deployments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site_config_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=120), nullable=False),
        sa.Column("environment", sa.String(length=80), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("payload", JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["site_config_id"], ["site_configs.id"]),
    )


def downgrade() -> None:
    for table in [
        "deployments",
        "assets",
        "site_configs",
        "outreach_drafts",
        "domain_candidates",
        "score_components",
        "full_opportunity_scores",
        "preliminary_assessments",
        "keyword_decisions",
        "keyword_clusters",
        "scan_plans",
        "api_calls",
    ]:
        op.drop_table(table)
    with op.batch_alter_table("raw_api_responses") as batch:
        batch.drop_constraint("fk_raw_api_responses_source_scan_run_id", type_="foreignkey")
        for column in [
            "expires_at",
            "checksum",
            "source_scan_run_id",
            "provider_request_id",
            "sanitized",
            "response_shape_version",
        ]:
            batch.drop_column(column)
    with op.batch_alter_table("scan_runs") as batch:
        batch.drop_constraint("fk_scan_runs_source_scan_run_id", type_="foreignkey")
        for column in [
            "cancel_requested",
            "retry_count",
            "partial_outputs",
            "progress_stage",
            "source_scan_run_id",
            "planned_cost_usd",
            "cache_policy_version",
            "scoring_version",
            "normalization_version",
            "adapter_versions",
            "adapter_names",
            "scan_profile",
            "data_mode",
        ]:
            batch.drop_column(column)
