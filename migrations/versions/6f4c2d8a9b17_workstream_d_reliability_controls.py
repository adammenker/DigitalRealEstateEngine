"""Add Workstream D worker reliability and paid-call controls.

Revision ID: 6f4c2d8a9b17
Revises: c9a4e7d2b6f1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "6f4c2d8a9b17"
down_revision = "c9a4e7d2b6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scan_runs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="4")
    )
    op.add_column(
        "scan_runs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("scan_runs", sa.Column("lease_token", sa.String(length=64), nullable=True))
    op.add_column(
        "scan_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "scan_runs", sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("scan_runs", sa.Column("quarantine_reason", sa.Text(), nullable=True))
    op.create_index("ix_scan_runs_lease_token", "scan_runs", ["lease_token"], unique=False)

    op.create_table(
        "provider_daily_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("usage_class", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("endpoint", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spend_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reserved_spend_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cache_miss_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unexpected_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("abnormal_cost_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("schema_drift_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "usage_date",
            "usage_class",
            "provider",
            "endpoint",
            name="uq_provider_daily_usage_bucket",
        ),
    )
    for column in ("usage_date", "usage_class", "provider", "endpoint"):
        op.create_index(f"ix_provider_daily_usage_{column}", "provider_daily_usage", [column])

    op.create_table(
        "provider_qualifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("adapter_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("provider", "environment", "adapter_version", "status", "expires_at"):
        op.create_index(f"ix_provider_qualifications_{column}", "provider_qualifications", [column])

    op.create_table(
        "billing_reconciliations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("internal_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("internal_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("provider_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("unmatched_provider_charges", sa.JSON(), nullable=False),
        sa.Column("unmatched_internal_calls", sa.JSON(), nullable=False),
        sa.Column("difference_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("source_filename", sa.String(length=240), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "provider",
        "environment",
        "period_start",
        "period_end",
        "reconciled_at",
        "status",
    ):
        op.create_index(f"ix_billing_reconciliations_{column}", "billing_reconciliations", [column])


def downgrade() -> None:
    op.drop_table("billing_reconciliations")
    op.drop_table("provider_qualifications")
    op.drop_table("provider_daily_usage")
    op.drop_index("ix_scan_runs_lease_token", table_name="scan_runs")
    op.drop_column("scan_runs", "quarantine_reason")
    op.drop_column("scan_runs", "quarantined_at")
    op.drop_column("scan_runs", "lease_expires_at")
    op.drop_column("scan_runs", "lease_token")
    op.drop_column("scan_runs", "next_attempt_at")
    op.drop_column("scan_runs", "max_attempts")
