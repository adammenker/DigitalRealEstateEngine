from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


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
    insufficient = "insufficient"


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
    intent_modifiers: list[str] = Field(default_factory=list)
    negative_product_terms: list[str] = Field(default_factory=list)
    provider_categories: list[str] = Field(default_factory=list)
    regulated: bool = False
    enabled: bool = True

    @model_validator(mode="after")
    def default_slug(self) -> ServiceFamily:
        self.slug = self.slug or slugify(self.id)
        return self


class Market(BaseModel):
    id: str
    slug: str | None = None
    display_name: str
    type: LocationType = LocationType.city
    country_code: str = "US"
    state: str | None = None
    cities: list[str] = Field(default_factory=list)
    postal_codes: list[str] = Field(default_factory=list)
    county: str | None = None
    county_fips: str | None = None
    metro: str | None = None
    metro_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    population: int | None = None
    reference_population: int | None = None
    aliases: list[str] = Field(default_factory=list)
    boundary_radius_km: float | None = None
    geography_id: str | None = None
    geography_dataset_version: str | None = None
    provider_location_code: str | None = None
    provider_location_name: str | None = None
    resolution_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def default_slug(self) -> Market:
        self.slug = self.slug or slugify(self.id)
        return self


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
    classification_confidence: float | None = None
    classifier_version: str = "v2"
    matched_rules: list[str] = Field(default_factory=list)
    classification_evidence: dict[str, Any] = Field(default_factory=dict)
    manual_override: str | None = None
    override_reason: str | None = None


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
    relevance_signals: dict[str, Any] = Field(default_factory=dict)
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
    categories: list[str] = Field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    rating: float | None = None
    review_count: int | None = None
    business_status: str = "unknown"
    contact_confidence: float | None = None
    source: str = "fixture"
    source_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_response_ref: str | None = None
    outreach_status: str = "not_contacted"
    suitability_score: float | None = None
    suitability_reasons: list[str] = Field(default_factory=list)
    suitability_signals: dict[str, Any] = Field(default_factory=dict)


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


class ScoreCalculationStep(BaseModel):
    label: str
    points: float
    detail: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class ScoreComponentDetail(BaseModel):
    score: float
    maximum_score: float
    formula: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    calculation_steps: list[ScoreCalculationStep] = Field(default_factory=list)
    explanation: str


class OpportunityScore(BaseModel):
    total_score: float
    component_scores: dict[str, float]
    input_measurements: dict[str, Any]
    missing_data_penalties: dict[str, float]
    scoring_version: str
    scoring_config_hash: str | None = None
    explanation: str
    confidence: Confidence
    component_explanations: dict[str, str] = Field(default_factory=dict)
    component_details: dict[str, ScoreComponentDetail] = Field(default_factory=dict)
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
