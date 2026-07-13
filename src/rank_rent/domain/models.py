from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


def slugify(value: str) -> str:
    chars = [c.lower() if c.isalnum() else "-" for c in value.strip()]
    slug = "".join(chars)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


class LocationType(StrEnum):
    city = "city"
    postal_code = "postal_code"
    county = "county"
    metro = "metro"
    market = "market"


class Confidence(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


class AvailabilityStatus(StrEnum):
    available = "available"
    unavailable = "unavailable"
    unknown = "unknown"
    premium_or_reserved = "premium_or_reserved"


class ServiceFamily(BaseModel):
    id: str
    slug: str | None = None
    display_name: str
    description: str = ""
    seed_queries: list[str] = Field(default_factory=list)
    negative_terms: list[str] = Field(default_factory=list)
    provider_categories: list[str] = Field(default_factory=list)
    regulated: bool = False
    enabled: bool = True

    @field_validator("slug", mode="before")
    @classmethod
    def default_slug(cls, value: str | None, info: Any) -> str:
        return value or slugify(info.data.get("id", "service"))


class Market(BaseModel):
    id: str
    slug: str | None = None
    display_name: str
    type: LocationType = LocationType.city
    country_code: str = "US"
    state: str | None = None
    cities: list[str] = Field(default_factory=list)
    postal_codes: list[str] = Field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    provider_location_code: str | None = None
    provider_location_name: str | None = None
    resolution_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("slug", mode="before")
    @classmethod
    def default_slug(cls, value: str | None, info: Any) -> str:
        return value or slugify(info.data.get("id", "market"))


class ResolvedLocation(BaseModel):
    original_input: str
    market: Market
    provider_location_code: str
    provider_location_name: str
    granularity: str
    notes: list[str] = Field(default_factory=list)


class KeywordCandidate(BaseModel):
    keyword: str
    source: str = "seed"
    included: bool = True
    excluded_reason: str | None = None


class KeywordMetric(BaseModel):
    keyword: str
    canonical_keyword: str
    intent: str
    search_volume: int | None = None
    cpc: float | None = None
    paid_competition: float | None = None
    monthly_history: list[int] = Field(default_factory=list)
    source: str = "fixture"
    source_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    market_granularity: str = "city"
    included: bool = True
    excluded_reason: str | None = None


class SerpResult(BaseModel):
    order: int
    result_type: str = "organic"
    url: str
    domain: str
    title: str
    description: str = ""
    classification: str = "unknown"
    is_local_provider: bool = False
    is_directory: bool = False
    is_national_brand: bool = False
    is_lead_generation_site: bool = False


class SerpSnapshot(BaseModel):
    query: str
    market_id: str
    device: str = "desktop"
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    features_present: list[str] = Field(default_factory=list)
    results: list[SerpResult] = Field(default_factory=list)
    raw_response_ref: str | None = None


class CompetitorMetric(BaseModel):
    url: str
    domain: str
    referring_domains: int | None = None
    backlinks: int | None = None
    authority: float | None = None
    page_relevance_score: float | None = None
    local_relevance: float | None = None
    page_type: str = "unknown"
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProviderCandidate(BaseModel):
    name: str
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    contact_form_url: str | None = None
    address: str | None = None
    service_area: str | None = None
    category: str | None = None
    rating: float | None = None
    review_count: int | None = None
    business_status: str = "unknown"
    contact_confidence: float | None = None
    source: str = "fixture"
    raw_response_ref: str | None = None
    outreach_status: str = "not_contacted"


class DomainAvailabilityResult(BaseModel):
    domain: str
    status: AvailabilityStatus
    provider_raw_status: str = "mock"
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DomainCandidate(BaseModel):
    domain: str
    pattern_used: str
    availability_status: AvailabilityStatus = AvailabilityStatus.unknown
    readability_score: float
    relevance_score: float
    brandability_score: float
    expansion_score: float
    risk_flags: list[str] = Field(default_factory=list)
    rank: int = 0
    checked_at: datetime | None = None


class OpportunityScore(BaseModel):
    total_score: float
    component_scores: dict[str, float]
    input_measurements: dict[str, Any]
    missing_data_penalties: dict[str, float]
    scoring_version: str
    explanation: str
    confidence: Confidence
    missing_fields: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OutreachDraft(BaseModel):
    provider_name: str
    type: str
    template_version: str = "v1"
    generated_body: str
    subject: str | None = None
    facts_used: dict[str, Any] = Field(default_factory=dict)
    generation_method: str = "deterministic_template"
    manually_edited_body: str | None = None
    status: str = "draft"


class Asset(BaseModel):
    type: str
    local_path: Path | None = None
    source_provider: str = "manual"
    source_url: str | None = None
    attribution: str | None = None
    license_metadata: dict[str, Any] = Field(default_factory=dict)
    alt_text: str
    approved: bool = False


class SiteConfig(BaseModel):
    property_brand: str
    domain_candidate: str | None = None
    service_family: ServiceFamily
    market: Market
    service_area_display_text: str
    contact_disclosure: str
    public_email_placeholder: str = "hello@example.com"
    public_phone_placeholder: str = "(000) 000-0000"
    services: list[str]
    faqs: list[dict[str, str]]
    pricing_guidance: str
    images: list[Asset] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    legal_disclosure_content: str


class DeploymentResult(BaseModel):
    provider: str
    url: str
    environment: str = "staging"
    commit_or_build_id: str | None = None
    status: str = "created"
    error_details: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
