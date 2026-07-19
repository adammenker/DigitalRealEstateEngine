"""add discovery completion evidence fields

Revision ID: d7f4a9c8b2e1
Revises: b4e9a1c2d7f6
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.types import JSON


revision = "d7f4a9c8b2e1"
down_revision = "b4e9a1c2d7f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scan_plan_calls") as batch:
        batch.add_column(sa.Column("planned_request_id", sa.String(length=80), nullable=True))
        batch.create_index("ix_scan_plan_calls_planned_request_id", ["planned_request_id"])

    with op.batch_alter_table("api_calls") as batch:
        batch.add_column(sa.Column("planned_request_id", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("error_type", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("provider_task_id", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("provider_request_id", sa.String(length=120), nullable=True))
        batch.create_index("ix_api_calls_planned_request_id", ["planned_request_id"])

    with op.batch_alter_table("keyword_decisions") as batch:
        batch.add_column(sa.Column("cluster_id", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("intent", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("search_volume", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cpc", sa.Float(), nullable=True))
        batch.add_column(sa.Column("granularity", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("ranking_score", sa.Float(), nullable=True))

    with op.batch_alter_table("serp_results") as batch:
        batch.add_column(sa.Column("classification_confidence", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column("classifier_version", sa.String(length=40), nullable=False, server_default="v2")
        )
        batch.add_column(sa.Column("matched_rules", JSON(), nullable=False, server_default="[]"))
        batch.add_column(
            sa.Column("classification_evidence", JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(sa.Column("manual_override", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("override_reason", sa.Text(), nullable=True))

    with op.batch_alter_table("competitor_metrics") as batch:
        batch.add_column(
            sa.Column("relevance_signals", JSON(), nullable=False, server_default="{}")
        )

    with op.batch_alter_table("provider_candidates") as batch:
        batch.add_column(sa.Column("suitability_score", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column("suitability_reasons", JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("provider_candidates") as batch:
        batch.drop_column("suitability_reasons")
        batch.drop_column("suitability_score")

    with op.batch_alter_table("competitor_metrics") as batch:
        batch.drop_column("relevance_signals")

    with op.batch_alter_table("serp_results") as batch:
        batch.drop_column("override_reason")
        batch.drop_column("manual_override")
        batch.drop_column("classification_evidence")
        batch.drop_column("matched_rules")
        batch.drop_column("classifier_version")
        batch.drop_column("classification_confidence")

    with op.batch_alter_table("keyword_decisions") as batch:
        batch.drop_column("ranking_score")
        batch.drop_column("granularity")
        batch.drop_column("cpc")
        batch.drop_column("search_volume")
        batch.drop_column("intent")
        batch.drop_column("cluster_id")

    with op.batch_alter_table("api_calls") as batch:
        batch.drop_index("ix_api_calls_planned_request_id")
        batch.drop_column("provider_request_id")
        batch.drop_column("provider_task_id")
        batch.drop_column("completed_at")
        batch.drop_column("started_at")
        batch.drop_column("error_type")
        batch.drop_column("planned_request_id")

    with op.batch_alter_table("scan_plan_calls") as batch:
        batch.drop_index("ix_scan_plan_calls_planned_request_id")
        batch.drop_column("planned_request_id")
