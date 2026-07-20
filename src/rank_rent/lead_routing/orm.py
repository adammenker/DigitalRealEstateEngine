from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from rank_rent.db.base import Base
from rank_rent.db.orm import TimestampMixin, now_utc


class PropertyRoutingProfileORM(TimestampMixin, Base):
    __tablename__ = "property_routing_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    public_tracking_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    public_contact_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    recording_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    recording_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    call_adapter_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    call_provider_route_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    routing_health_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    routing_health_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ProviderAssignmentORM(TimestampMixin, Base):
    __tablename__ = "provider_assignments"
    __table_args__ = (
        Index(
            "uq_provider_assignments_active_property",
            "property_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(
        ForeignKey("property_routing_profiles.property_id"), index=True
    )
    provider_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_candidates.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(40), default="candidate", index=True)
    public_business_name: Mapped[str] = mapped_column(String(240))
    destination_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    destination_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response_expectation_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lead_acceptance_required: Mapped[bool] = mapped_column(Boolean, default=True)
    agreement_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    agreement_ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    termination_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    replaced_assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_assignments.id"), nullable=True
    )


class LeadORM(TimestampMixin, Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("property_id", "idempotency_key", name="uq_lead_property_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(
        ForeignKey("property_routing_profiles.property_id"), index=True
    )
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    provider_assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_assignments.id"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    dedupe_hash: Mapped[str] = mapped_column(String(64), index=True)
    subject_hash: Mapped[str] = mapped_column(String(64), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    pii_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LeadEventORM(TimestampMixin, Base):
    __tablename__ = "lead_events"
    __table_args__ = (UniqueConstraint("lead_id", "event_key", name="uq_lead_event_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    event_key: Mapped[str] = mapped_column(String(160))
    truth_basis: Mapped[str] = mapped_column(String(40))
    source_type: Mapped[str] = mapped_column(String(40))
    source_name: Mapped[str] = mapped_column(String(120))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ConsentRecordORM(TimestampMixin, Base):
    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), unique=True)
    consent_granted: Mapped[bool] = mapped_column(Boolean)
    consent_text: Mapped[str] = mapped_column(Text)
    consent_text_version: Mapped[str] = mapped_column(String(80))
    referral_disclosure_acknowledged: Mapped[bool] = mapped_column(Boolean)
    referral_disclosure_text: Mapped[str] = mapped_column(Text)
    referral_disclosure_version: Mapped[str] = mapped_column(String(80))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    proof_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SpamAssessmentORM(TimestampMixin, Base):
    __tablename__ = "spam_assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), unique=True)
    score: Mapped[float] = mapped_column(Float)
    disposition: Mapped[str] = mapped_column(String(40))
    signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    assessor_version: Mapped[str] = mapped_column(String(80))


class RoutingAttemptORM(TimestampMixin, Base):
    __tablename__ = "routing_attempts"
    __table_args__ = (
        UniqueConstraint("delivery_key", "attempt_number", name="uq_delivery_attempt_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), index=True)
    provider_assignment_id: Mapped[int] = mapped_column(ForeignKey("provider_assignments.id"))
    channel: Mapped[str] = mapped_column(String(40))
    delivery_key: Mapped[str] = mapped_column(String(180), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProviderDeliveryORM(TimestampMixin, Base):
    __tablename__ = "provider_deliveries"
    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_provider_deliveries_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "max_attempts >= 1",
            name="ck_provider_deliveries_max_attempts_positive",
        ),
        Index(
            "ix_provider_deliveries_queue",
            "status",
            "next_attempt_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), index=True)
    provider_assignment_id: Mapped[int] = mapped_column(ForeignKey("provider_assignments.id"))
    delivery_key: Mapped[str] = mapped_column(String(180), unique=True)
    channel: Mapped[str] = mapped_column(String(40))
    destination_reference: Mapped[str] = mapped_column(String(254))
    adapter_name: Mapped[str] = mapped_column(String(120))
    provider_message_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LeadOutcomeORM(TimestampMixin, Base):
    __tablename__ = "lead_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "lead_id", "source_name", "source_event_id", name="uq_lead_outcome_source"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), index=True)
    outcome_type: Mapped[str] = mapped_column(String(80))
    truth_basis: Mapped[str] = mapped_column(String(40))
    source_type: Mapped[str] = mapped_column(String(40))
    source_name: Mapped[str] = mapped_column(String(120))
    source_event_id: Mapped[str] = mapped_column(String(160))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class AnalyticsEventORM(TimestampMixin, Base):
    __tablename__ = "analytics_events"
    __table_args__ = (
        UniqueConstraint(
            "source_name",
            "source_event_id",
            name="uq_analytics_event_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(String(120), index=True)
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    truth_basis: Mapped[str] = mapped_column(String(40))
    source_type: Mapped[str] = mapped_column(String(40))
    source_name: Mapped[str] = mapped_column(String(120))
    source_event_id: Mapped[str] = mapped_column(String(160))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    value_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
