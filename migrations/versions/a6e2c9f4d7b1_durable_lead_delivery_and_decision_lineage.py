"""add durable lead delivery and property decision scan lineage

Revision ID: a6e2c9f4d7b1
Revises: 8a7d3f2c1b90
Create Date: 2026-07-20 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a6e2c9f4d7b1"
down_revision = "8a7d3f2c1b90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provider_deliveries",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "provider_deliveries",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "provider_deliveries",
        sa.Column("worker_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "provider_deliveries",
        sa.Column("lease_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "provider_deliveries",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "provider_deliveries",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "provider_deliveries",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "provider_deliveries",
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "provider_deliveries",
        sa.Column("last_error_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "provider_deliveries",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_provider_deliveries_lease_token",
        "provider_deliveries",
        ["lease_token"],
    )
    op.create_index(
        "ix_provider_deliveries_queue",
        "provider_deliveries",
        ["status", "next_attempt_at", "id"],
    )
    with op.batch_alter_table("provider_deliveries") as batch:
        batch.create_check_constraint(
            "ck_provider_deliveries_attempt_count_nonnegative",
            "attempt_count >= 0",
        )
        batch.create_check_constraint(
            "ck_provider_deliveries_max_attempts_positive",
            "max_attempts >= 1",
        )

    op.add_column(
        "json_artifacts",
        sa.Column("scan_run_id", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("json_artifacts") as batch:
        batch.create_foreign_key(
            "fk_json_artifacts_scan_run_id",
            "scan_runs",
            ["scan_run_id"],
            ["id"],
        )
    op.create_index(
        "ix_json_artifacts_scan_run_id",
        "json_artifacts",
        ["scan_run_id"],
    )

    op.add_column(
        "property_decisions",
        sa.Column("scan_run_id", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE property_decisions
            SET scan_run_id = (
                SELECT full_opportunity_scores.scan_run_id
                FROM full_opportunity_scores
                WHERE full_opportunity_scores.id = property_decisions.full_score_id
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE json_artifacts
            SET scan_run_id = (
                SELECT property_decisions.scan_run_id
                FROM property_decisions
                WHERE property_decisions.evidence_snapshot_id = json_artifacts.id
            )
            WHERE EXISTS (
                SELECT 1
                FROM property_decisions
                WHERE property_decisions.evidence_snapshot_id = json_artifacts.id
            )
            """
        )
    )

    with op.batch_alter_table("full_opportunity_scores") as batch:
        batch.create_unique_constraint(
            "uq_full_opportunity_scores_id_scan_run",
            ["id", "scan_run_id"],
        )
    with op.batch_alter_table("json_artifacts") as batch:
        batch.create_unique_constraint(
            "uq_json_artifacts_id_scan_run",
            ["id", "scan_run_id"],
        )
    with op.batch_alter_table("property_decisions") as batch:
        batch.alter_column("scan_run_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_property_decisions_scan_run_id",
            "scan_runs",
            ["scan_run_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_property_decision_score_scan",
            "full_opportunity_scores",
            ["full_score_id", "scan_run_id"],
            ["id", "scan_run_id"],
        )
        batch.create_foreign_key(
            "fk_property_decision_evidence_scan",
            "json_artifacts",
            ["evidence_snapshot_id", "scan_run_id"],
            ["id", "scan_run_id"],
        )
    op.create_index(
        "ix_property_decisions_scan_run_id",
        "property_decisions",
        ["scan_run_id"],
    )
    _create_property_decision_immutability()


def downgrade() -> None:
    _drop_property_decision_immutability()
    op.drop_index(
        "ix_property_decisions_scan_run_id",
        table_name="property_decisions",
    )
    with op.batch_alter_table("property_decisions") as batch:
        batch.drop_constraint("fk_property_decision_evidence_scan", type_="foreignkey")
        batch.drop_constraint("fk_property_decision_score_scan", type_="foreignkey")
        batch.drop_constraint("fk_property_decisions_scan_run_id", type_="foreignkey")
        batch.drop_column("scan_run_id")
    with op.batch_alter_table("json_artifacts") as batch:
        batch.drop_constraint("uq_json_artifacts_id_scan_run", type_="unique")
    with op.batch_alter_table("full_opportunity_scores") as batch:
        batch.drop_constraint("uq_full_opportunity_scores_id_scan_run", type_="unique")
    op.drop_index("ix_json_artifacts_scan_run_id", table_name="json_artifacts")
    with op.batch_alter_table("json_artifacts") as batch:
        batch.drop_constraint("fk_json_artifacts_scan_run_id", type_="foreignkey")
        batch.drop_column("scan_run_id")

    with op.batch_alter_table("provider_deliveries") as batch:
        batch.drop_constraint(
            "ck_provider_deliveries_max_attempts_positive",
            type_="check",
        )
        batch.drop_constraint(
            "ck_provider_deliveries_attempt_count_nonnegative",
            type_="check",
        )
    op.drop_index("ix_provider_deliveries_queue", table_name="provider_deliveries")
    op.drop_index("ix_provider_deliveries_lease_token", table_name="provider_deliveries")
    for column in (
        "completed_at",
        "last_error_summary",
        "last_error_code",
        "lease_expires_at",
        "heartbeat_at",
        "claimed_at",
        "lease_token",
        "worker_id",
        "next_attempt_at",
        "max_attempts",
    ):
        op.drop_column("provider_deliveries", column)


def _create_property_decision_immutability() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_property_decision_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'property_decision_is_immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER property_decisions_immutable_update
            BEFORE UPDATE ON property_decisions
            FOR EACH ROW EXECUTE FUNCTION reject_property_decision_mutation()
            """
        )
        op.execute(
            """
            CREATE TRIGGER property_decisions_immutable_delete
            BEFORE DELETE ON property_decisions
            FOR EACH ROW EXECUTE FUNCTION reject_property_decision_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER property_decisions_immutable_update
            BEFORE UPDATE ON property_decisions
            BEGIN
                SELECT RAISE(ABORT, 'property_decision_is_immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER property_decisions_immutable_delete
            BEFORE DELETE ON property_decisions
            BEGIN
                SELECT RAISE(ABORT, 'property_decision_is_immutable');
            END
            """
        )


def _drop_property_decision_immutability() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS property_decisions_immutable_update "
            "ON property_decisions"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS property_decisions_immutable_delete "
            "ON property_decisions"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_property_decision_mutation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS property_decisions_immutable_update")
        op.execute("DROP TRIGGER IF EXISTS property_decisions_immutable_delete")
