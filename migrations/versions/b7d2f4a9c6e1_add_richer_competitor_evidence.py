"""add richer competitor evidence

Revision ID: b7d2f4a9c6e1
Revises: a3c7e5f9b1d4
Create Date: 2026-07-19 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7d2f4a9c6e1"
down_revision = "a3c7e5f9b1d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("competitor_metrics") as batch:
        batch.add_column(sa.Column("page_url", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("normalized_domain", sa.String(length=240), nullable=True)
        )
        batch.add_column(sa.Column("page_referring_domains", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("page_backlinks", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("page_authority", sa.Float(), nullable=True))
        batch.add_column(sa.Column("domain_referring_domains", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("domain_backlinks", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("domain_authority", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column(
                "page_metrics_available",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "domain_metrics_available",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "serp_observation_records",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )

    op.execute(
        """
        UPDATE competitor_metrics
        SET page_url = url,
            normalized_domain = domain,
            domain_referring_domains = referring_domains,
            domain_backlinks = backlinks,
            domain_authority = authority,
            domain_metrics_available = CASE
                WHEN referring_domains IS NOT NULL
                  OR backlinks IS NOT NULL
                  OR authority IS NOT NULL
                THEN TRUE ELSE FALSE END,
            serp_observation_records = serp_observations
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("competitor_metrics") as batch:
        batch.drop_column("serp_observation_records")
        batch.drop_column("domain_metrics_available")
        batch.drop_column("page_metrics_available")
        batch.drop_column("domain_authority")
        batch.drop_column("domain_backlinks")
        batch.drop_column("domain_referring_domains")
        batch.drop_column("page_authority")
        batch.drop_column("page_backlinks")
        batch.drop_column("page_referring_domains")
        batch.drop_column("normalized_domain")
        batch.drop_column("page_url")
