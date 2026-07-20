"""add raw response blob metadata

Revision ID: c9a4e7d2b6f1
Revises: f8c1d4e7a2b9
Create Date: 2026-07-19 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9a4e7d2b6f1"
down_revision = "f8c1d4e7a2b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("raw_api_responses") as batch:
        batch.add_column(sa.Column("object_key", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("storage_backend", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("content_type", sa.String(length=160), nullable=True))
        batch.add_column(sa.Column("size_bytes", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column(
                "retention_classification",
                sa.String(length=80),
                nullable=False,
                server_default="raw_provider_response",
            )
        )
        batch.add_column(
            sa.Column(
                "encryption_status",
                sa.String(length=80),
                nullable=False,
                server_default="not_encrypted",
            )
        )
        batch.add_column(sa.Column("blob_created_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint(
            "uq_raw_api_responses_object_key",
            ["object_key"],
        )
        batch.create_check_constraint(
            "ck_raw_api_responses_size_bytes_nonnegative",
            "size_bytes IS NULL OR size_bytes >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("raw_api_responses") as batch:
        batch.drop_constraint(
            "ck_raw_api_responses_size_bytes_nonnegative",
            type_="check",
        )
        batch.drop_constraint("uq_raw_api_responses_object_key", type_="unique")
        batch.drop_column("blob_created_at")
        batch.drop_column("encryption_status")
        batch.drop_column("retention_classification")
        batch.drop_column("size_bytes")
        batch.drop_column("content_type")
        batch.drop_column("storage_backend")
        batch.drop_column("object_key")
