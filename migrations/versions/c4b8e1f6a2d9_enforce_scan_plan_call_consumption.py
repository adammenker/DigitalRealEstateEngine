"""enforce scan plan call consumption

Revision ID: c4b8e1f6a2d9
Revises: f2a6c9d4e8b1
Create Date: 2026-07-18 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "c4b8e1f6a2d9"
down_revision = "f2a6c9d4e8b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scan_plan_calls") as batch:
        batch.create_unique_constraint(
            "uq_scan_plan_calls_scan_planned_request",
            ["scan_run_id", "planned_request_id"],
        )
    with op.batch_alter_table("api_calls") as batch:
        batch.create_unique_constraint(
            "uq_api_calls_scan_planned_request",
            ["scan_run_id", "planned_request_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("api_calls") as batch:
        batch.drop_constraint(
            "uq_api_calls_scan_planned_request",
            type_="unique",
        )
    with op.batch_alter_table("scan_plan_calls") as batch:
        batch.drop_constraint(
            "uq_scan_plan_calls_scan_planned_request",
            type_="unique",
        )
