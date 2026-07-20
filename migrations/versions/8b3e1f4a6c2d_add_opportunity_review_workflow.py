"""add opportunity review and approval workflow

Revision ID: 8b3e1f4a6c2d
Revises: 1a7d9c4e6b20
Create Date: 2026-07-20 04:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8b3e1f4a6c2d"
down_revision = "1a7d9c4e6b20"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
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


def upgrade() -> None:
    with op.batch_alter_table("opportunities") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.String(length=120), nullable=True))
        batch.add_column(
            sa.Column(
                "review_version",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.create_index(
            "ix_opportunities_owner_user_id",
            ["owner_user_id"],
            unique=False,
        )

    op.execute(
        """
        UPDATE opportunities
        SET status = CASE status
          WHEN 'approved' THEN 'approved_for_property'
          WHEN 'evidence_rejected' THEN 'needs_more_evidence'
          WHEN 'scan_failed' THEN 'needs_more_evidence'
          WHEN 'partial_review' THEN 'needs_more_evidence'
          WHEN 'unusable_review' THEN 'needs_more_evidence'
          ELSE status
        END
        """
    )

    op.create_table(
        "opportunity_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("prior_state", sa.String(length=40), nullable=True),
        sa.Column("review_state", sa.String(length=40), nullable=False),
        sa.Column("owner_user_id", sa.String(length=120), nullable=True),
        sa.Column("reviewer_user_id", sa.String(length=120), nullable=False),
        sa.Column("reviewer_role", sa.String(length=40), nullable=False),
        sa.Column("decision", sa.String(length=80), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
    )
    op.create_index(
        "ix_opportunity_reviews_opportunity_id",
        "opportunity_reviews",
        ["opportunity_id"],
    )
    op.create_index(
        "ix_opportunity_reviews_review_state",
        "opportunity_reviews",
        ["review_state"],
    )
    op.create_index(
        "ix_opportunity_reviews_owner_user_id",
        "opportunity_reviews",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_opportunity_reviews_reviewer_user_id",
        "opportunity_reviews",
        ["reviewer_user_id"],
    )

    op.create_table(
        "evidence_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("override_kind", sa.String(length=60), nullable=False),
        sa.Column("target_record_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(length=120), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("original_value", sa.JSON(), nullable=False),
        sa.Column("new_value", sa.JSON(), nullable=False),
        sa.Column("actor_user_id", sa.String(length=120), nullable=False),
        sa.Column("actor_role", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("score_impact", sa.Float(), nullable=False),
        sa.Column("score_impact_explanation", sa.Text(), nullable=False),
        sa.Column("reverses_override_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(
            ["reverses_override_id"],
            ["evidence_overrides.id"],
        ),
        sa.UniqueConstraint(
            "reverses_override_id",
            name="uq_evidence_override_reversal",
        ),
    )
    op.create_index(
        "ix_evidence_overrides_opportunity_id",
        "evidence_overrides",
        ["opportunity_id"],
    )
    op.create_index(
        "ix_evidence_overrides_override_kind",
        "evidence_overrides",
        ["override_kind"],
    )
    op.create_index(
        "ix_evidence_overrides_actor_user_id",
        "evidence_overrides",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_evidence_overrides_reverses_override_id",
        "evidence_overrides",
        ["reverses_override_id"],
    )

    op.create_table(
        "discovery_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("owner_user_id", sa.String(length=120), nullable=False),
        sa.Column("service_family_id", sa.Integer(), nullable=False),
        sa.Column("market_filters", sa.JSON(), nullable=False),
        sa.Column("prefilter_profile", sa.String(length=80), nullable=False),
        sa.Column("testing_profile", sa.String(length=80), nullable=False),
        sa.Column("full_profile", sa.String(length=80), nullable=False),
        sa.Column("budget_usd", sa.Float(), nullable=False),
        sa.Column("freshness_requirements", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["service_family_id"], ["service_families.id"]),
        sa.UniqueConstraint(
            "owner_user_id",
            "name",
            name="uq_discovery_template_owner_name",
        ),
    )
    op.create_index(
        "ix_discovery_templates_owner_user_id",
        "discovery_templates",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_discovery_templates_active",
        "discovery_templates",
        ["active"],
    )

    op.create_table(
        "batch_scan_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column("scan_profile", sa.String(length=40), nullable=False),
        sa.Column("data_mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("aggregate_budget_usd", sa.Float(), nullable=False),
        sa.Column("aggregate_estimated_cost_usd", sa.Float(), nullable=False),
        sa.Column("approved_max_cost_usd", sa.Float(), nullable=True),
        sa.Column("confirmed_by", sa.String(length=120), nullable=True),
        sa.Column("confirmation_reason", sa.Text(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["template_id"], ["discovery_templates.id"]),
    )
    op.create_index(
        "ix_batch_scan_plans_created_by",
        "batch_scan_plans",
        ["created_by"],
    )
    op.create_index(
        "ix_batch_scan_plans_status",
        "batch_scan_plans",
        ["status"],
    )

    op.create_table(
        "batch_scan_plan_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_plan_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False),
        sa.Column("scan_plan_payload", sa.JSON(), nullable=False),
        sa.Column("scan_run_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["batch_plan_id"], ["batch_scan_plans.id"]),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
        sa.UniqueConstraint(
            "batch_plan_id",
            "opportunity_id",
            name="uq_batch_scan_plan_opportunity",
        ),
        sa.UniqueConstraint("scan_run_id"),
    )
    op.create_index(
        "ix_batch_scan_plan_items_batch_plan_id",
        "batch_scan_plan_items",
        ["batch_plan_id"],
    )
    op.create_index(
        "ix_batch_scan_plan_items_opportunity_id",
        "batch_scan_plan_items",
        ["opportunity_id"],
    )


def downgrade() -> None:
    op.drop_table("batch_scan_plan_items")
    op.drop_table("batch_scan_plans")
    op.drop_table("discovery_templates")
    op.drop_table("evidence_overrides")
    op.drop_table("opportunity_reviews")
    with op.batch_alter_table("opportunities") as batch:
        batch.drop_index("ix_opportunities_owner_user_id")
        batch.drop_column("review_version")
        batch.drop_column("owner_user_id")
