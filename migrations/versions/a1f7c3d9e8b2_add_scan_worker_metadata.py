"""add scan worker metadata

Revision ID: a1f7c3d9e8b2
Revises: 4d2a7c9e1f03
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "a1f7c3d9e8b2"
down_revision = "4d2a7c9e1f03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scan_runs") as batch:
        batch.add_column(sa.Column("worker_id", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scan_runs") as batch:
        batch.drop_column("heartbeat_at")
        batch.drop_column("claimed_at")
        batch.drop_column("worker_id")
