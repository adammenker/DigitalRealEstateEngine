"""Add lease-safe provider attempts and executable qualification evidence.

Revision ID: 1a7d9c4e6b20
Revises: a6e2c9f4d7b1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "1a7d9c4e6b20"
down_revision = "a6e2c9f4d7b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("api_calls") as batch:
        batch.add_column(sa.Column("attempt_token", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("attempt_state", sa.String(length=40), nullable=False, server_default="prepared")
        )
        batch.add_column(
            sa.Column(
                "provider_outcome", sa.String(length=40), nullable=False, server_default="not_sent"
            )
        )
        batch.add_column(
            sa.Column("reservation_state", sa.String(length=40), nullable=False, server_default="none")
        )
        batch.add_column(sa.Column("reservation_usage_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("reservation_usage_class", sa.String(length=20), nullable=True))
        batch.add_column(
            sa.Column(
                "reservation_estimated_cost_usd", sa.Float(), nullable=False, server_default="0"
            )
        )
        batch.add_column(sa.Column("network_started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("execution_worker_id", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("execution_lease_token", sa.String(length=64), nullable=True))
        batch.create_unique_constraint("uq_api_calls_attempt_token", ["attempt_token"])
        batch.create_index("ix_api_calls_attempt_token", ["attempt_token"])
        batch.create_index("ix_api_calls_attempt_state", ["attempt_state"])
        batch.create_index("ix_api_calls_provider_outcome", ["provider_outcome"])
        batch.create_index("ix_api_calls_reservation_state", ["reservation_state"])
    op.execute(
        sa.text(
            """
            UPDATE api_calls
            SET
                attempt_state = CASE
                    WHEN status = 'completed' THEN 'completed'
                    WHEN status = 'cache_hit' THEN 'cache_hit'
                    WHEN status IN ('running', 'in_flight') THEN 'provider_outcome_unknown'
                    WHEN status = 'blocked' THEN 'blocked'
                    ELSE 'failed'
                END,
                provider_outcome = CASE
                    WHEN status = 'completed' THEN 'completed'
                    WHEN status IN ('running', 'in_flight') THEN 'unknown'
                    WHEN status IN ('cache_hit', 'blocked') THEN 'not_sent'
                    ELSE 'failed'
                END,
                status = CASE
                    WHEN status IN ('running', 'in_flight') THEN 'provider_outcome_unknown'
                    ELSE status
                END
            """
        )
    )

    with op.batch_alter_table("provider_daily_usage") as batch:
        batch.add_column(
            sa.Column("unreconciled_spend_usd", sa.Float(), nullable=False, server_default="0")
        )

    with op.batch_alter_table("provider_qualifications") as batch:
        batch.add_column(
            sa.Column(
                "execution_method",
                sa.String(length=40),
                nullable=False,
                server_default="manual_import",
            )
        )
        batch.add_column(
            sa.Column("gate_eligible", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("evidence_sha256", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("executed_by", sa.String(length=160), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("override_reason", sa.Text(), nullable=False, server_default="")
        )
        batch.create_index("ix_provider_qualifications_execution_method", ["execution_method"])
        batch.create_index("ix_provider_qualifications_gate_eligible", ["gate_eligible"])
        batch.create_index("ix_provider_qualifications_evidence_sha256", ["evidence_sha256"])


def downgrade() -> None:
    with op.batch_alter_table("provider_qualifications") as batch:
        batch.drop_index("ix_provider_qualifications_evidence_sha256")
        batch.drop_index("ix_provider_qualifications_gate_eligible")
        batch.drop_index("ix_provider_qualifications_execution_method")
        batch.drop_column("override_reason")
        batch.drop_column("executed_by")
        batch.drop_column("evidence_sha256")
        batch.drop_column("gate_eligible")
        batch.drop_column("execution_method")

    with op.batch_alter_table("provider_daily_usage") as batch:
        batch.drop_column("unreconciled_spend_usd")

    with op.batch_alter_table("api_calls") as batch:
        batch.drop_index("ix_api_calls_reservation_state")
        batch.drop_index("ix_api_calls_provider_outcome")
        batch.drop_index("ix_api_calls_attempt_state")
        batch.drop_index("ix_api_calls_attempt_token")
        batch.drop_constraint("uq_api_calls_attempt_token", type_="unique")
        batch.drop_column("execution_lease_token")
        batch.drop_column("execution_worker_id")
        batch.drop_column("reconciled_at")
        batch.drop_column("network_started_at")
        batch.drop_column("reservation_estimated_cost_usd")
        batch.drop_column("reservation_usage_class")
        batch.drop_column("reservation_usage_date")
        batch.drop_column("reservation_state")
        batch.drop_column("provider_outcome")
        batch.drop_column("attempt_state")
        batch.drop_column("attempt_token")
