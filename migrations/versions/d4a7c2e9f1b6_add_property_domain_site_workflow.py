"""add property, domain, and site-production workflow

Revision ID: d4a7c2e9f1b6
Revises: 6a1c9e4b7d20
Create Date: 2026-07-20 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4a7c2e9f1b6"
down_revision = "6a1c9e4b7d20"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "properties",
        sa.Column("id", sa.String(length=120), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("neutral_brand", sa.String(length=160), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=True),
        sa.Column("service_family_id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("public_tracking_number", sa.String(length=40), nullable=True),
        sa.Column("public_contact_email", sa.String(length=254), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("active_site_config_version", sa.Integer(), nullable=True),
        sa.Column("analytics_config", sa.JSON(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["service_family_id"], ["service_families.id"]),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.UniqueConstraint("opportunity_id"),
        sa.UniqueConstraint("domain"),
    )
    op.create_index("ix_properties_opportunity_id", "properties", ["opportunity_id"])
    op.create_index("ix_properties_status", "properties", ["status"])

    # Workstream J may already have routing profiles. Reconcile one profile per
    # opportunity into a provider-independent property without changing assignment IDs.
    op.execute(
        """
        INSERT INTO properties (
          id, opportunity_id, neutral_brand, service_family_id, market_id,
          public_tracking_number, public_contact_email, status,
          analytics_config, current_version, created_at, updated_at
        )
        SELECT
          profile.property_id,
          profile.opportunity_id,
          'Legacy property ' || profile.property_id,
          opportunity.service_family_id,
          opportunity.market_id,
          profile.public_tracking_number,
          profile.public_contact_email,
          'draft',
          '{}',
          1,
          CURRENT_TIMESTAMP,
          CURRENT_TIMESTAMP
        FROM property_routing_profiles AS profile
        JOIN opportunities AS opportunity ON opportunity.id = profile.opportunity_id
        WHERE NOT EXISTS (
          SELECT 1
          FROM property_routing_profiles AS earlier
          WHERE earlier.opportunity_id = profile.opportunity_id
            AND earlier.id < profile.id
        )
        """
    )

    op.create_table(
        "property_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("changed_by", sa.String(length=120), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"]),
        sa.UniqueConstraint("property_id", "version", name="uq_property_version"),
    )
    op.create_index("ix_property_versions_property_id", "property_versions", ["property_id"])

    op.create_table(
        "property_domain_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("generation_method", sa.String(length=80), nullable=False),
        sa.Column("availability_status", sa.String(length=40), nullable=False),
        sa.Column("availability_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("availability_evidence", sa.JSON(), nullable=False),
        sa.Column("decision_by", sa.String(length=120), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"]),
        sa.UniqueConstraint(
            "property_id",
            "domain",
            name="uq_property_domain_candidate",
        ),
    )
    op.create_index(
        "ix_property_domain_candidates_property_id",
        "property_domain_candidates",
        ["property_id"],
    )
    op.create_index(
        "ix_property_domain_candidates_domain",
        "property_domain_candidates",
        ["domain"],
    )
    op.create_index(
        "ix_property_domain_candidates_status",
        "property_domain_candidates",
        ["status"],
    )

    op.create_table(
        "domain_registrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("domain_candidate_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("registrar_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("purchase_approved", sa.Boolean(), nullable=False),
        sa.Column("purchase_approved_by", sa.String(length=120), nullable=False),
        sa.Column("purchase_approval_reason", sa.Text(), nullable=False),
        sa.Column("purchase_approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("external_reference", sa.String(length=240), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_dns_records", sa.JSON(), nullable=False),
        sa.Column("observed_dns_records", sa.JSON(), nullable=False),
        sa.Column("dns_evidence_reference", sa.Text(), nullable=True),
        sa.Column("dns_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dns_verified_by", sa.String(length=120), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"]),
        sa.ForeignKeyConstraint(
            ["domain_candidate_id"],
            ["property_domain_candidates.id"],
        ),
        sa.UniqueConstraint("domain_candidate_id"),
        sa.UniqueConstraint("domain"),
    )
    op.create_index(
        "ix_domain_registrations_property_id",
        "domain_registrations",
        ["property_id"],
    )
    op.create_index("ix_domain_registrations_status", "domain_registrations", ["status"])

    op.create_table(
        "property_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("asset_type", sa.String(length=80), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("source_provider", sa.String(length=120), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("attribution", sa.Text(), nullable=True),
        sa.Column("license_metadata", sa.JSON(), nullable=False),
        sa.Column("alt_text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("approval_reason", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"]),
    )
    op.create_index("ix_property_assets_property_id", "property_assets", ["property_id"])
    op.create_index("ix_property_assets_approved", "property_assets", ["approved"])

    with op.batch_alter_table("provider_assignments") as batch:
        batch.add_column(sa.Column("logo_asset_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("hours", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch.add_column(
            sa.Column(
                "service_radius",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        for name in (
            "credentials",
            "license_numbers",
            "approved_claims",
            "attributed_testimonials",
            "provider_photos",
        ):
            batch.add_column(
                sa.Column(
                    name,
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'[]'"),
                )
            )
        batch.add_column(sa.Column("claims_reviewed_by", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("claims_reviewed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("claims_review_reason", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("activation_approved_by", sa.String(length=120), nullable=True)
        )
        batch.add_column(sa.Column("activation_reason", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("replacement_approved_by", sa.String(length=120), nullable=True)
        )
        batch.create_foreign_key(
            "fk_provider_assignments_logo_asset",
            "property_assets",
            ["logo_asset_id"],
            ["id"],
        )

    op.create_table(
        "property_site_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False),
        sa.Column("config_payload", sa.JSON(), nullable=False),
        sa.Column("config_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("approval_reason", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"]),
        sa.UniqueConstraint("property_id", "version", name="uq_site_config_version"),
    )
    op.create_index(
        "ix_property_site_configs_property_id",
        "property_site_configs",
        ["property_id"],
    )
    op.create_index(
        "ix_property_site_configs_status",
        "property_site_configs",
        ["status"],
    )

    op.create_table(
        "site_builds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("site_config_id", sa.Integer(), nullable=False),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("builder_version", sa.String(length=40), nullable=False),
        sa.Column("build_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"]),
        sa.ForeignKeyConstraint(["site_config_id"], ["property_site_configs.id"]),
        sa.UniqueConstraint(
            "site_config_id",
            "environment",
            "build_sha256",
            name="uq_reproducible_site_build",
        ),
    )
    op.create_index("ix_site_builds_property_id", "site_builds", ["property_id"])
    op.create_index("ix_site_builds_site_config_id", "site_builds", ["site_config_id"])
    op.create_index("ix_site_builds_environment", "site_builds", ["environment"])
    op.create_index("ix_site_builds_build_sha256", "site_builds", ["build_sha256"])
    op.create_index("ix_site_builds_status", "site_builds", ["status"])

    op.create_table(
        "compliance_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("site_config_id", sa.Integer(), nullable=False),
        sa.Column("site_build_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("checklist", sa.JSON(), nullable=False),
        sa.Column("validation_snapshot", sa.JSON(), nullable=False),
        sa.Column("reviewer_user_id", sa.String(length=120), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"]),
        sa.ForeignKeyConstraint(["site_config_id"], ["property_site_configs.id"]),
        sa.ForeignKeyConstraint(["site_build_id"], ["site_builds.id"]),
    )
    op.create_index(
        "ix_compliance_reviews_property_id",
        "compliance_reviews",
        ["property_id"],
    )
    op.create_index(
        "ix_compliance_reviews_site_config_id",
        "compliance_reviews",
        ["site_config_id"],
    )
    op.create_index(
        "ix_compliance_reviews_site_build_id",
        "compliance_reviews",
        ["site_build_id"],
    )
    op.create_index("ix_compliance_reviews_status", "compliance_reviews", ["status"])
    op.create_index(
        "ix_compliance_reviews_reviewer_user_id",
        "compliance_reviews",
        ["reviewer_user_id"],
    )

    op.create_table(
        "property_deployments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("site_build_id", sa.Integer(), nullable=False),
        sa.Column("domain_registration_id", sa.Integer(), nullable=True),
        sa.Column("provider_assignment_id", sa.Integer(), nullable=True),
        sa.Column("compliance_review_id", sa.Integer(), nullable=True),
        sa.Column("previous_deployment_id", sa.Integer(), nullable=True),
        sa.Column("rollback_of_deployment_id", sa.Integer(), nullable=True),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("adapter_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("local_only", sa.Boolean(), nullable=False),
        sa.Column("operator_user_id", sa.String(length=120), nullable=False),
        sa.Column("operator_confirmation", sa.Boolean(), nullable=False),
        sa.Column("confirmation_reason", sa.Text(), nullable=False),
        sa.Column("neutral_pilot_mode", sa.Boolean(), nullable=False),
        sa.Column("gate_snapshot", sa.JSON(), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"]),
        sa.ForeignKeyConstraint(["site_build_id"], ["site_builds.id"]),
        sa.ForeignKeyConstraint(["domain_registration_id"], ["domain_registrations.id"]),
        sa.ForeignKeyConstraint(["provider_assignment_id"], ["provider_assignments.id"]),
        sa.ForeignKeyConstraint(["compliance_review_id"], ["compliance_reviews.id"]),
        sa.ForeignKeyConstraint(
            ["previous_deployment_id"],
            ["property_deployments.id"],
        ),
        sa.ForeignKeyConstraint(
            ["rollback_of_deployment_id"],
            ["property_deployments.id"],
        ),
    )
    op.create_index(
        "ix_property_deployments_property_id",
        "property_deployments",
        ["property_id"],
    )
    op.create_index(
        "ix_property_deployments_current",
        "property_deployments",
        ["property_id", "environment", "status"],
    )
    op.create_index(
        "ix_property_deployments_environment",
        "property_deployments",
        ["environment"],
    )
    op.create_index(
        "ix_property_deployments_status",
        "property_deployments",
        ["status"],
    )


def downgrade() -> None:
    op.drop_table("property_deployments")
    op.drop_table("compliance_reviews")
    op.drop_table("site_builds")
    op.drop_table("property_site_configs")
    with op.batch_alter_table("provider_assignments") as batch:
        batch.drop_constraint(
            "fk_provider_assignments_logo_asset",
            type_="foreignkey",
        )
        for name in (
            "replacement_approved_by",
            "activation_reason",
            "activation_approved_by",
            "claims_review_reason",
            "claims_reviewed_at",
            "claims_reviewed_by",
            "provider_photos",
            "attributed_testimonials",
            "approved_claims",
            "license_numbers",
            "credentials",
            "service_radius",
            "hours",
            "logo_asset_id",
        ):
            batch.drop_column(name)
    op.drop_table("property_assets")
    op.drop_table("domain_registrations")
    op.drop_table("property_domain_candidates")
    op.drop_table("property_versions")
    op.drop_table("properties")
