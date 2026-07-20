"""Version immutable raw responses behind logical cache pointers.

Revision ID: 8a7d3f2c1b90
Revises: 6f4c2d8a9b17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "8a7d3f2c1b90"
down_revision = "6f4c2d8a9b17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_response_cache_entries",
        sa.Column("cache_key", sa.String(length=128), primary_key=True),
        sa.Column("raw_api_response_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["raw_api_response_id"],
            ["raw_api_responses.id"],
            name="fk_raw_response_cache_entries_response",
        ),
    )
    op.create_index(
        "ix_raw_response_cache_entries_raw_api_response_id",
        "raw_response_cache_entries",
        ["raw_api_response_id"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO raw_response_cache_entries (
                cache_key, raw_api_response_id, created_at, updated_at
            )
            SELECT cache_key, id, created_at, updated_at
            FROM raw_api_responses
            """
        )
    )

    with op.batch_alter_table("raw_api_responses") as batch:
        batch.drop_constraint("uq_raw_api_responses_object_key", type_="unique")
        batch.drop_index("ix_raw_api_responses_cache_key")
        batch.create_index("ix_raw_api_responses_cache_key", ["cache_key"], unique=False)


def downgrade() -> None:
    connection = op.get_bind()
    duplicate_count = connection.scalar(
        sa.text(
            """
            SELECT COUNT(*)
            FROM (
                SELECT cache_key
                FROM raw_api_responses
                GROUP BY cache_key
                HAVING COUNT(*) > 1
            ) AS duplicate_keys
            """
        )
    )
    duplicate_object_count = connection.scalar(
        sa.text(
            """
            SELECT COUNT(*)
            FROM (
                SELECT object_key
                FROM raw_api_responses
                WHERE object_key IS NOT NULL
                GROUP BY object_key
                HAVING COUNT(*) > 1
            ) AS duplicate_objects
            """
        )
    )
    if duplicate_count or duplicate_object_count:
        raise RuntimeError(
            "Cannot downgrade versioned raw-response storage after multiple immutable "
            "versions have been recorded."
        )

    with op.batch_alter_table("raw_api_responses") as batch:
        batch.drop_index("ix_raw_api_responses_cache_key")
        batch.create_index("ix_raw_api_responses_cache_key", ["cache_key"], unique=True)
        batch.create_unique_constraint(
            "uq_raw_api_responses_object_key",
            ["object_key"],
        )

    op.drop_index(
        "ix_raw_response_cache_entries_raw_api_response_id",
        table_name="raw_response_cache_entries",
    )
    op.drop_table("raw_response_cache_entries")
