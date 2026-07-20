from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class LeadChannel(StrEnum):
    form = "form"
    phone = "phone"


class DeliveryChannel(StrEnum):
    email = "email"
    phone = "phone"


class ProviderAssignmentStatus(StrEnum):
    candidate = "candidate"
    pilot = "pilot"
    active = "active"
    paused = "paused"
    terminated = "terminated"
    replaced = "replaced"


class LeadStatus(StrEnum):
    received = "received"
    spam = "spam"
    routing = "routing"
    delivered = "delivered"
    delivery_failed = "delivery_failed"
    deleted = "deleted"


class DeliveryStatus(StrEnum):
    pending = "pending"
    leased = "leased"
    delivering = "delivering"
    retrying = "retrying"
    delivered = "delivered"
    failed = "failed"
    cancelled = "cancelled"
    outcome_unknown = "outcome_unknown"


class TruthBasis(StrEnum):
    observed = "observed"
    provider_reported = "provider_reported"
    operator_verified = "operator_verified"
    estimated = "estimated"


class AnalyticsSourceType(StrEnum):
    web_analytics = "web_analytics"
    form = "form"
    call_tracking = "call_tracking"
    provider = "provider"
    operator = "operator"
    system = "system"


class AnalyticsEventType(StrEnum):
    organic_landing = "organic_landing"
    form_start = "form_start"
    form_submit = "form_submit"
    qualified_lead = "qualified_lead"
    call = "call"
    answered_call = "answered_call"
    missed_call = "missed_call"
    provider_delivery = "provider_delivery"
    provider_acceptance = "provider_acceptance"
    appointment = "appointment"
    won_job = "won_job"
    lost_job = "lost_job"
    revenue = "revenue"


class LeadAccessRole(StrEnum):
    operator = "operator"
    privacy_admin = "privacy_admin"
    provider = "provider"
    analytics = "analytics"


class LeadForm(BaseModel):
    property_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    postal_code: str | None = Field(default=None, max_length=20)
    message: str | None = Field(default=None, max_length=2000)
    consent_accepted: bool
    consent_text_version: str = Field(min_length=1, max_length=80)
    referral_disclosure_acknowledged: bool
    referral_disclosure_version: str = Field(min_length=1, max_length=80)
    idempotency_key: str = Field(
        min_length=12,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    honeypot: str = Field(default="", max_length=200)

    @field_validator("name", "message")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split())

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Enter a valid email address.")
        return normalized

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        digits = "".join(character for character in value if character.isdigit())
        if not 10 <= len(digits) <= 15:
            raise ValueError("Enter a valid phone number.")
        return f"+{digits}"

    @model_validator(mode="after")
    def validate_contact_and_consent(self) -> LeadForm:
        if self.email is None and self.phone is None:
            raise ValueError("An email address or phone number is required.")
        if not self.consent_accepted:
            raise ValueError("Consent is required.")
        if not self.referral_disclosure_acknowledged:
            raise ValueError("Referral disclosure acknowledgement is required.")
        return self


class RequestContext(BaseModel):
    remote_address: str | None = Field(default=None, max_length=80)
    user_agent: str | None = Field(default=None, max_length=500)
    request_id: str = Field(min_length=1, max_length=120)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SpamAssessmentResult(BaseModel):
    score: float = Field(ge=0, le=1)
    disposition: str = Field(pattern=r"^(allow|review|block)$")
    signals: list[str] = Field(default_factory=list)
    assessor_version: str = "local-v1"


class LeadSubmissionResult(BaseModel):
    lead_id: str
    status: LeadStatus
    idempotent_replay: bool = False
    duplicate: bool = False
    delivery_ids: list[str] = Field(default_factory=list)


class DeliveryRequest(BaseModel):
    delivery_key: str
    property_id: str
    lead_id: str
    provider_assignment_id: int
    channel: DeliveryChannel
    destination: str
    contact_name: str
    contact_email: str | None = None
    contact_phone: str | None = None
    message: str | None = None


class DeliveryResult(BaseModel):
    provider_message_id: str
    accepted: bool
    status: str
    retryable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CallRouteRequest(BaseModel):
    property_id: str
    public_number: str
    destination_number: str
    recording_enabled: bool = False
    recording_retention_days: int | None = Field(default=None, ge=1, le=3650)


class CallRouteResult(BaseModel):
    provider_route_id: str
    public_number: str
    status: str


class RoutingHealth(BaseModel):
    healthy: bool
    status: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AnalyticsEventInput(BaseModel):
    property_id: str = Field(min_length=1, max_length=120)
    event_type: AnalyticsEventType
    source_type: AnalyticsSourceType
    source_name: str = Field(min_length=1, max_length=120)
    source_event_id: str = Field(min_length=1, max_length=160)
    truth_basis: TruthBasis
    lead_id: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    value_usd: float | None = Field(default=None, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_truth_source(self) -> AnalyticsEventInput:
        if self.truth_basis == TruthBasis.provider_reported:
            if self.source_type != AnalyticsSourceType.provider:
                raise ValueError("Provider-reported events must use the provider source type.")
        if self.truth_basis == TruthBasis.operator_verified:
            if self.source_type != AnalyticsSourceType.operator:
                raise ValueError("Operator-verified events must use the operator source type.")
        if self.event_type == AnalyticsEventType.revenue and self.value_usd is None:
            raise ValueError("Revenue events require value_usd.")
        return self


class LeadRoutingPolicy(BaseModel):
    dedupe_window_minutes: int = Field(default=60, ge=1, le=10080)
    rate_limit_count: int = Field(default=5, ge=1, le=1000)
    rate_limit_window_seconds: int = Field(default=300, ge=1, le=86400)
    maximum_delivery_attempts: int = Field(default=3, ge=1, le=10)
    delivery_retry_base_seconds: float = Field(default=30.0, gt=0, le=86400)
    delivery_retry_max_seconds: float = Field(default=3600.0, gt=0, le=604800)
    retention_days: int = Field(default=365, ge=1, le=3650)
    recording_enabled: bool = False
    recording_retention_days: int | None = Field(default=None, ge=1, le=3650)
    consent_text: str = Field(
        default="I consent to being contacted about this service request.",
        min_length=10,
        max_length=2000,
    )
    consent_text_version: str = Field(default="consent-v1", min_length=1, max_length=80)
    referral_disclosure_text: str = Field(
        default=("This property may refer your request to an independent service provider."),
        min_length=10,
        max_length=2000,
    )
    referral_disclosure_version: str = Field(
        default="referral-v1",
        min_length=1,
        max_length=80,
    )
    pii_hash_pepper: str = Field(min_length=16, exclude=True)

    @model_validator(mode="after")
    def validate_recording(self) -> LeadRoutingPolicy:
        if self.recording_enabled and self.recording_retention_days is None:
            raise ValueError("Recorded calls require an explicit retention period.")
        return self


class AccessContext(BaseModel):
    actor_id: str = Field(min_length=1, max_length=120)
    role: LeadAccessRole
    provider_assignment_ids: set[int] = Field(default_factory=set)
