from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class PropertyStatus(StrEnum):
    draft = "draft"
    staging = "staging"
    production = "production"
    archived = "archived"


class DomainCandidateStatus(StrEnum):
    generated = "generated"
    shortlisted = "shortlisted"
    rejected = "rejected"


class DomainAvailability(StrEnum):
    unchecked = "unchecked"
    available = "available"
    unavailable = "unavailable"
    unknown = "unknown"


class DomainRegistrationStatus(StrEnum):
    purchase_approved = "purchase_approved"
    manually_registered = "manually_registered"
    dns_verified = "dns_verified"


class SiteConfigStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    superseded = "superseded"


class BuildEnvironment(StrEnum):
    preview = "preview"
    staging = "staging"
    production = "production"


class DeploymentStatus(StrEnum):
    deployed = "deployed"
    rolled_back = "rolled_back"
    failed = "failed"


class ComplianceStatus(StrEnum):
    approved = "approved"
    rejected = "rejected"


class PropertyCreateRequest(BaseModel):
    neutral_brand: str = Field(min_length=3, max_length=160)
    property_id: str | None = Field(
        default=None,
        min_length=3,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
    )
    public_tracking_number: str | None = Field(default=None, max_length=40)
    public_contact_email: str | None = Field(default=None, max_length=254)
    analytics_config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("neutral_brand")
    @classmethod
    def normalize_brand(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if any(
            token in normalized.lower()
            for token in ("official", "licensed contractor", "government")
        ):
            raise ValueError("The neutral brand must not imply an unsupported identity.")
        return normalized

    @field_validator("public_contact_email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Enter a valid public contact email.")
        return normalized


class PropertyUpdateRequest(BaseModel):
    neutral_brand: str | None = Field(default=None, min_length=3, max_length=160)
    public_tracking_number: str | None = Field(default=None, max_length=40)
    public_contact_email: str | None = Field(default=None, max_length=254)
    analytics_config: dict[str, Any] | None = None
    reason: str = Field(min_length=3, max_length=500)


class DomainGenerationRequest(BaseModel):
    limit: int = Field(default=6, ge=1, le=20)
    tlds: list[str] = Field(default_factory=lambda: ["com"])

    @field_validator("tlds")
    @classmethod
    def validate_tlds(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            item = value.strip().lower().lstrip(".")
            if not re.fullmatch(r"[a-z]{2,12}", item):
                raise ValueError("TLDs must contain only letters.")
            normalized.append(item)
        return list(dict.fromkeys(normalized))


class DomainDecisionRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class DomainAvailabilityRequest(BaseModel):
    fixture_status: DomainAvailability
    evidence: dict[str, Any] = Field(default_factory=dict)


class DomainPurchaseApprovalRequest(BaseModel):
    explicit_purchase_approval: bool
    reason: str = Field(min_length=8, max_length=1000)


class ManualRegistrationRequest(BaseModel):
    external_reference: str = Field(min_length=3, max_length=240)
    registrar_name: str = Field(default="manual", min_length=2, max_length=120)
    expected_dns_records: dict[str, str] = Field(default_factory=dict)


class DNSVerificationRequest(BaseModel):
    observed_records: dict[str, str] = Field(default_factory=dict)
    evidence_reference: str = Field(min_length=3, max_length=500)


class AssetCreateRequest(BaseModel):
    asset_type: str = Field(min_length=2, max_length=80)
    local_path: str | None = Field(default=None, max_length=1000)
    source_provider: str = Field(min_length=2, max_length=120)
    source_url: str | None = Field(default=None, max_length=2000)
    attribution: str | None = Field(default=None, max_length=1000)
    license_metadata: dict[str, Any] = Field(default_factory=dict)
    alt_text: str = Field(min_length=3, max_length=500)
    content_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )

    @model_validator(mode="after")
    def validate_provenance(self) -> AssetCreateRequest:
        if self.local_path is None and self.source_url is None:
            raise ValueError("Asset provenance requires a local path or source URL.")
        if not self.license_metadata:
            raise ValueError("Asset license metadata is required.")
        return self


class AssetApprovalRequest(BaseModel):
    approved: bool
    reason: str = Field(min_length=3, max_length=500)


class ProviderAssignmentInput(BaseModel):
    provider_candidate_id: int | None = None
    public_business_name: str = Field(min_length=2, max_length=240)
    destination_phone: str | None = Field(default=None, max_length=40)
    destination_email: str | None = Field(default=None, max_length=254)
    logo_asset_id: int | None = None
    hours: dict[str, Any] = Field(default_factory=dict)
    service_radius: dict[str, Any] = Field(default_factory=dict)
    credentials: list[dict[str, Any]] = Field(default_factory=list)
    license_numbers: list[dict[str, Any]] = Field(default_factory=list)
    approved_claims: list[dict[str, Any]] = Field(default_factory=list)
    attributed_testimonials: list[dict[str, Any]] = Field(default_factory=list)
    provider_photos: list[int] = Field(default_factory=list)
    claims_review_reason: str = Field(min_length=8, max_length=1000)

    @model_validator(mode="after")
    def validate_contact(self) -> ProviderAssignmentInput:
        if self.destination_phone is None and self.destination_email is None:
            raise ValueError("A provider destination phone or email is required.")
        for testimonial in self.attributed_testimonials:
            if not testimonial.get("source") or not testimonial.get("attribution"):
                raise ValueError("Testimonials require source and attribution.")
        for credential in [*self.credentials, *self.license_numbers]:
            if not credential.get("source"):
                raise ValueError("Credentials and licenses require an evidence source.")
        return self


class ProviderActivationRequest(BaseModel):
    reason: str = Field(min_length=8, max_length=1000)


class ProviderReplacementRequest(BaseModel):
    replacement_assignment_id: int
    reason: str = Field(min_length=8, max_length=1000)


class SiteConfigInput(BaseModel):
    brand: dict[str, Any]
    service: dict[str, Any]
    market: dict[str, Any]
    pricing_guidance: dict[str, Any]
    service_process: list[dict[str, Any]]
    faqs: list[dict[str, str]]
    local_considerations: list[dict[str, Any]]
    provider_details: dict[str, Any] = Field(default_factory=dict)
    referral_disclosure: str = Field(min_length=40, max_length=2000)
    calls_to_action: list[dict[str, Any]]
    asset_ids: list[int] = Field(default_factory=list)
    metadata: dict[str, Any]
    analytics: dict[str, Any] = Field(default_factory=dict)
    form_routing: dict[str, Any] = Field(default_factory=dict)
    change_reason: str = Field(min_length=3, max_length=1000)

    @field_validator("referral_disclosure")
    @classmethod
    def validate_disclosure(cls, value: str) -> str:
        normalized = " ".join(value.split())
        lowered = normalized.lower()
        if "referral" not in lowered or "independent" not in lowered:
            raise ValueError(
                "The disclosure must clearly describe the independent referral relationship."
            )
        return normalized

    @model_validator(mode="after")
    def validate_content(self) -> SiteConfigInput:
        if not self.service_process:
            raise ValueError("At least one service-process step is required.")
        if not self.faqs:
            raise ValueError("At least one FAQ is required.")
        if not self.calls_to_action:
            raise ValueError("At least one call to action is required.")
        title = str(self.metadata.get("title", "")).strip()
        description = str(self.metadata.get("description", "")).strip()
        if not title or not description:
            raise ValueError("Metadata title and description are required.")
        serialized = str(self.model_dump()).lower()
        prohibited = (
            '"@type": "localbusiness"',
            "'@type': 'localbusiness'",
            "guaranteed ranking",
            "best contractor in",
        )
        if any(item in serialized for item in prohibited):
            raise ValueError("Site configuration contains an unsupported claim or identity.")
        return self


class SiteConfigApprovalRequest(BaseModel):
    approved: bool
    reason: str = Field(min_length=8, max_length=1000)


class SiteBuildRequest(BaseModel):
    environment: BuildEnvironment


class ComplianceReviewRequest(BaseModel):
    approved: bool
    checklist: dict[str, bool]
    notes: str = Field(min_length=8, max_length=2000)


class DeploymentRequest(BaseModel):
    environment: BuildEnvironment
    operator_confirmation: bool = False
    confirmation_reason: str = Field(default="", max_length=1000)
    neutral_pilot_mode: bool = False
    neutral_pilot_reason: str = Field(default="", max_length=1000)


class RollbackRequest(BaseModel):
    target_deployment_id: int
    operator_confirmation: bool
    reason: str = Field(min_length=8, max_length=1000)
