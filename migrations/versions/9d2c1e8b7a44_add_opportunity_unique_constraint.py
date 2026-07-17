"""add opportunity service-market uniqueness

Revision ID: 9d2c1e8b7a44
Revises: 35a8b4f9eb77
Create Date: 2026-07-13 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


revision = "9d2c1e8b7a44"
down_revision = "35a8b4f9eb77"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "uq_opportunity_service_market"
TABLE_NAME = "opportunities"


def _has_constraint() -> bool:
    constraints = inspect(op.get_bind()).get_unique_constraints(TABLE_NAME)
    return any(constraint.get("name") == CONSTRAINT_NAME for constraint in constraints)


def upgrade() -> None:
    if _has_constraint():
        return
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        batch_op.create_unique_constraint(
            CONSTRAINT_NAME,
            ["service_family_id", "market_id"],
        )


def downgrade() -> None:
    if not _has_constraint():
        return
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
