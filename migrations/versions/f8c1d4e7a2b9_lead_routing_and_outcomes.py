"""add lead routing, provider operations, and outcome calibration records

Revision ID: f8c1d4e7a2b9
Revises: b7d2f4a9c6e1
Create Date: 2026-07-19 23:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f8c1d4e7a2b9"
down_revision = "b7d2f4a9c6e1"
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
        "property_routing_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("public_tracking_number", sa.String(length=40), nullable=True),
        sa.Column("public_contact_email", sa.String(length=254), nullable=True),
        sa.Column("recording_approved", sa.Boolean(), nullable=False),
        sa.Column("recording_retention_days", sa.Integer(), nullable=True),
        sa.Column("call_adapter_name", sa.String(length=120), nullable=True),
        sa.Column("call_provider_route_id", sa.String(length=180), nullable=True),
        sa.Column("routing_health_status", sa.String(length=80), nullable=True),
        sa.Column("routing_health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
    )
    op.create_index(
        "ix_property_routing_profiles_property_id",
        "property_routing_profiles",
        ["property_id"],
        unique=True,
    )

    op.create_table(
        "provider_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("provider_candidate_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("public_business_name", sa.String(length=240), nullable=False),
        sa.Column("destination_phone", sa.String(length=40), nullable=True),
        sa.Column("destination_email", sa.String(length=254), nullable=True),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column("response_expectation_minutes", sa.Integer(), nullable=True),
        sa.Column("lead_acceptance_required", sa.Boolean(), nullable=False),
        sa.Column("agreement_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agreement_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("termination_reason", sa.Text(), nullable=True),
        sa.Column("replaced_assignment_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["property_routing_profiles.property_id"],
        ),
        sa.ForeignKeyConstraint(
            ["provider_candidate_id"],
            ["provider_candidates.id"],
        ),
        sa.ForeignKeyConstraint(
            ["replaced_assignment_id"],
            ["provider_assignments.id"],
        ),
    )
    op.create_index(
        "ix_provider_assignments_property_id",
        "provider_assignments",
        ["property_id"],
    )
    op.create_index(
        "ix_provider_assignments_status",
        "provider_assignments",
        ["status"],
    )
    op.create_index(
        "uq_provider_assignments_active_property",
        "provider_assignments",
        ["property_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "leads",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("provider_assignment_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("dedupe_hash", sa.String(length=64), nullable=False),
        sa.Column("subject_hash", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pii_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["property_routing_profiles.property_id"],
        ),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(
            ["provider_assignment_id"],
            ["provider_assignments.id"],
        ),
        sa.UniqueConstraint(
            "property_id",
            "idempotency_key",
            name="uq_lead_property_idempotency",
        ),
    )
    op.create_index("ix_leads_property_id", "leads", ["property_id"])
    op.create_index("ix_leads_status", "leads", ["status"])
    op.create_index("ix_leads_dedupe_hash", "leads", ["dedupe_hash"])
    op.create_index("ix_leads_subject_hash", "leads", ["subject_hash"])

    op.create_table(
        "lead_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("event_key", sa.String(length=160), nullable=False),
        sa.Column("truth_basis", sa.String(length=40), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.UniqueConstraint("lead_id", "event_key", name="uq_lead_event_key"),
    )
    op.create_index("ix_lead_events_lead_id", "lead_events", ["lead_id"])

    op.create_table(
        "consent_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("consent_granted", sa.Boolean(), nullable=False),
        sa.Column("consent_text", sa.Text(), nullable=False),
        sa.Column("consent_text_version", sa.String(length=80), nullable=False),
        sa.Column("referral_disclosure_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("referral_disclosure_text", sa.Text(), nullable=False),
        sa.Column("referral_disclosure_version", sa.String(length=80), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("proof_metadata", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
    )

    op.create_table(
        "spam_assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("disposition", sa.String(length=40), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("assessor_version", sa.String(length=80), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
    )

    op.create_table(
        "routing_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_id", sa.String(length=36), nullable=False),
        sa.Column("provider_assignment_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("delivery_key", sa.String(length=180), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(
            ["provider_assignment_id"],
            ["provider_assignments.id"],
        ),
        sa.UniqueConstraint(
            "delivery_key",
            "attempt_number",
            name="uq_delivery_attempt_number",
        ),
    )
    op.create_index("ix_routing_attempts_lead_id", "routing_attempts", ["lead_id"])
    op.create_index(
        "ix_routing_attempts_delivery_key",
        "routing_attempts",
        ["delivery_key"],
    )

    op.create_table(
        "provider_deliveries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("lead_id", sa.String(length=36), nullable=False),
        sa.Column("provider_assignment_id", sa.Integer(), nullable=False),
        sa.Column("delivery_key", sa.String(length=180), nullable=False, unique=True),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("destination_reference", sa.String(length=254), nullable=False),
        sa.Column("adapter_name", sa.String(length=120), nullable=False),
        sa.Column("provider_message_id", sa.String(length=180), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(
            ["provider_assignment_id"],
            ["provider_assignments.id"],
        ),
    )
    op.create_index(
        "ix_provider_deliveries_lead_id",
        "provider_deliveries",
        ["lead_id"],
    )

    op.create_table(
        "lead_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_id", sa.String(length=36), nullable=False),
        sa.Column("outcome_type", sa.String(length=80), nullable=False),
        sa.Column("truth_basis", sa.String(length=40), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("source_event_id", sa.String(length=160), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_usd", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.UniqueConstraint(
            "lead_id",
            "source_name",
            "source_event_id",
            name="uq_lead_outcome_source",
        ),
    )
    op.create_index("ix_lead_outcomes_lead_id", "lead_outcomes", ["lead_id"])

    op.create_table(
        "analytics_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("lead_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("truth_basis", sa.String(length=40), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("source_event_id", sa.String(length=160), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_usd", sa.Float(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.UniqueConstraint(
            "source_name",
            "source_event_id",
            name="uq_analytics_event_source",
        ),
    )
    op.create_index("ix_analytics_events_property_id", "analytics_events", ["property_id"])
    op.create_index("ix_analytics_events_event_type", "analytics_events", ["event_type"])
    op.create_index("ix_analytics_events_occurred_at", "analytics_events", ["occurred_at"])

    op.create_table(
        "property_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.String(length=120), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("full_score_id", sa.Integer(), nullable=False),
        sa.Column("evidence_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("score_version_at_selection", sa.String(length=80), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_family_slug", sa.String(length=120), nullable=False),
        sa.Column("market_size_band", sa.String(length=80), nullable=False),
        sa.Column("evidence_quality", sa.String(length=40), nullable=False),
        sa.Column("validated_opportunity_cost_usd", sa.Float(), nullable=False),
        sa.Column("selection_context", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["full_score_id"], ["full_opportunity_scores.id"]),
        sa.ForeignKeyConstraint(["evidence_snapshot_id"], ["json_artifacts.id"]),
    )
    op.create_index(
        "ix_property_decisions_property_id",
        "property_decisions",
        ["property_id"],
        unique=True,
    )
    op.create_index(
        "ix_property_decisions_opportunity_id",
        "property_decisions",
        ["opportunity_id"],
    )
    op.create_index(
        "ix_property_decisions_service_family_slug",
        "property_decisions",
        ["service_family_slug"],
    )
    op.create_index(
        "ix_property_decisions_market_size_band",
        "property_decisions",
        ["market_size_band"],
    )
    op.create_index(
        "ix_property_decisions_evidence_quality",
        "property_decisions",
        ["evidence_quality"],
    )

    op.create_table(
        "property_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_decision_id", sa.Integer(), nullable=False),
        sa.Column("period_date", sa.Date(), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("source_record_id", sa.String(length=160), nullable=False),
        sa.Column("truth_basis", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.String(length=40), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("average_position", sa.Float(), nullable=True),
        sa.Column("organic_sessions", sa.Integer(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("forms", sa.Integer(), nullable=False),
        sa.Column("qualified_leads", sa.Integer(), nullable=False),
        sa.Column("appointments", sa.Integer(), nullable=False),
        sa.Column("won_jobs", sa.Integer(), nullable=False),
        sa.Column("reported_revenue", sa.Float(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_suitability_score", sa.Float(), nullable=True),
        sa.Column("addressable_market_score", sa.Float(), nullable=True),
        sa.Column("metadata_payload", sa.JSON(), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["property_decision_id"],
            ["property_decisions.id"],
        ),
        sa.UniqueConstraint(
            "property_decision_id",
            "source_name",
            "source_record_id",
            name="uq_property_outcome_source",
        ),
    )
    op.create_index(
        "ix_property_outcomes_property_decision_id",
        "property_outcomes",
        ["property_decision_id"],
    )
    op.create_index(
        "ix_property_outcomes_period_date",
        "property_outcomes",
        ["period_date"],
    )
    op.create_index(
        "ix_property_outcomes_truth_basis",
        "property_outcomes",
        ["truth_basis"],
    )

    op.create_table(
        "calibration_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_version", sa.String(length=80), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("property_count", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        *_timestamps(),
    )

    op.create_table(
        "scoring_change_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("proposal_id", sa.String(length=120), nullable=False, unique=True),
        sa.Column("current_version", sa.String(length=80), nullable=False),
        sa.Column("proposed_version", sa.String(length=80), nullable=False),
        sa.Column("initiated_by", sa.String(length=120), nullable=False),
        sa.Column("reviewer_id", sa.String(length=120), nullable=False),
        sa.Column("benchmark_run_id", sa.String(length=120), nullable=False),
        sa.Column("benchmark_passed", sa.Boolean(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("authorized_for_manual_application", sa.Boolean(), nullable=False),
        sa.Column("applied_automatically", sa.Boolean(), nullable=False),
        *_timestamps(),
    )


def downgrade() -> None:
    op.drop_table("scoring_change_reviews")
    op.drop_table("calibration_reports")
    op.drop_index("ix_property_outcomes_truth_basis", table_name="property_outcomes")
    op.drop_index("ix_property_outcomes_period_date", table_name="property_outcomes")
    op.drop_index(
        "ix_property_outcomes_property_decision_id",
        table_name="property_outcomes",
    )
    op.drop_table("property_outcomes")
    op.drop_index(
        "ix_property_decisions_evidence_quality",
        table_name="property_decisions",
    )
    op.drop_index(
        "ix_property_decisions_market_size_band",
        table_name="property_decisions",
    )
    op.drop_index(
        "ix_property_decisions_service_family_slug",
        table_name="property_decisions",
    )
    op.drop_index(
        "ix_property_decisions_opportunity_id",
        table_name="property_decisions",
    )
    op.drop_index(
        "ix_property_decisions_property_id",
        table_name="property_decisions",
    )
    op.drop_table("property_decisions")
    op.drop_index("ix_analytics_events_occurred_at", table_name="analytics_events")
    op.drop_index("ix_analytics_events_event_type", table_name="analytics_events")
    op.drop_index("ix_analytics_events_property_id", table_name="analytics_events")
    op.drop_table("analytics_events")
    op.drop_index("ix_lead_outcomes_lead_id", table_name="lead_outcomes")
    op.drop_table("lead_outcomes")
    op.drop_index("ix_provider_deliveries_lead_id", table_name="provider_deliveries")
    op.drop_table("provider_deliveries")
    op.drop_index("ix_routing_attempts_delivery_key", table_name="routing_attempts")
    op.drop_index("ix_routing_attempts_lead_id", table_name="routing_attempts")
    op.drop_table("routing_attempts")
    op.drop_table("spam_assessments")
    op.drop_table("consent_records")
    op.drop_index("ix_lead_events_lead_id", table_name="lead_events")
    op.drop_table("lead_events")
    op.drop_index("ix_leads_subject_hash", table_name="leads")
    op.drop_index("ix_leads_dedupe_hash", table_name="leads")
    op.drop_index("ix_leads_status", table_name="leads")
    op.drop_index("ix_leads_property_id", table_name="leads")
    op.drop_table("leads")
    op.drop_index(
        "uq_provider_assignments_active_property",
        table_name="provider_assignments",
    )
    op.drop_index("ix_provider_assignments_status", table_name="provider_assignments")
    op.drop_index(
        "ix_provider_assignments_property_id",
        table_name="provider_assignments",
    )
    op.drop_table("provider_assignments")
    op.drop_index(
        "ix_property_routing_profiles_property_id",
        table_name="property_routing_profiles",
    )
    op.drop_table("property_routing_profiles")
