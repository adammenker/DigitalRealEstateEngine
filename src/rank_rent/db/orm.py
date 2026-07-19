from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from rank_rent.db.base import Base


def now_utc() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class ServiceFamilyORM(TimestampMixin, Base):
    __tablename__ = "service_families"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    seed_queries: Mapped[list[str]] = mapped_column(JSON, default=list)
    negative_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    intent_modifiers: Mapped[list[str]] = mapped_column(JSON, default=list)
    negative_product_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    provider_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    regulated: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)


class MarketORM(TimestampMixin, Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(40), default="city")
    country_code: Mapped[str] = mapped_column(String(2), default="US")
    state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cities: Mapped[list[str]] = mapped_column(JSON, default=list)
    postal_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_location_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider_location_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolution_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class OpportunityORM(TimestampMixin, Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint("service_family_id", "market_id", name="uq_opportunity_service_market"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_family_id: Mapped[int] = mapped_column(ForeignKey("service_families.id"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    status: Mapped[str] = mapped_column(String(40), default="discovered")
    latest_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    missing_data_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    service_family: Mapped[ServiceFamilyORM] = relationship()
    market: Mapped[MarketORM] = relationship()


class ScanRunORM(TimestampMixin, Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default="pending")
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    actual_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    integration_versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    data_mode: Mapped[str] = mapped_column(String(20), default="fixture")
    scan_profile: Mapped[str] = mapped_column(String(40), default="testing")
    adapter_names: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    adapter_versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    normalization_version: Mapped[str] = mapped_column(String(40), default="v1")
    scoring_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cache_policy_version: Mapped[str] = mapped_column(String(40), default="v2")
    planned_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    source_scan_run_id: Mapped[int | None] = mapped_column(ForeignKey("scan_runs.id"), nullable=True)
    progress_stage: Mapped[str] = mapped_column(String(80), default="queued")
    partial_outputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScanPlanCallORM(TimestampMixin, Base):
    __tablename__ = "scan_plan_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    planned_request_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(80))
    endpoint: Mapped[str] = mapped_column(String(160))
    stage: Mapped[str] = mapped_column(String(80))
    request_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cache_key: Mapped[str] = mapped_column(String(128), index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    request_known: Mapped[bool] = mapped_column(Boolean, default=True)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    required: Mapped[bool] = mapped_column(Boolean, default=True)


class RawApiResponseORM(TimestampMixin, Base):
    __tablename__ = "raw_api_responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(80))
    endpoint: Mapped[str] = mapped_column(String(120))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    api_version: Mapped[str] = mapped_column(String(40), default="fixture")
    response_shape_version: Mapped[str] = mapped_column(String(40), default="v1")
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sanitized: Mapped[bool] = mapped_column(Boolean, default=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    response_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    provider_task_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_scan_run_id: Mapped[int | None] = mapped_column(ForeignKey("scan_runs.id"), nullable=True)
    checksum: Mapped[str] = mapped_column(String(128), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiCallORM(TimestampMixin, Base):
    __tablename__ = "api_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int | None] = mapped_column(ForeignKey("scan_runs.id"), nullable=True)
    planned_request_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    raw_api_response_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_api_responses.id"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(80))
    endpoint: Mapped[str] = mapped_column(String(160))
    stage: Mapped[str] = mapped_column(String(80))
    cache_key: Mapped[str] = mapped_column(String(128), index=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    force_refresh: Mapped[bool] = mapped_column(Boolean, default=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    actual_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(80), default="planned")
    error_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_task_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class ScanPlanORM(TimestampMixin, Base):
    __tablename__ = "scan_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    scan_profile: Mapped[str] = mapped_column(String(40))
    cache_hit_count: Mapped[int] = mapped_column(Integer, default=0)
    paid_call_count: Mapped[int] = mapped_column(Integer, default=0)
    estimated_uncached_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    maximum_allowed_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    confirmation_required: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class KeywordClusterORM(TimestampMixin, Base):
    __tablename__ = "keyword_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    representative_keyword: Mapped[str] = mapped_column(String(240))
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    dedupe_method: Mapped[str] = mapped_column(String(80), default="exact")
    combined_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)


class KeywordDecisionORM(TimestampMixin, Base):
    __tablename__ = "keyword_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    keyword: Mapped[str] = mapped_column(String(240))
    canonical_keyword: Mapped[str] = mapped_column(String(240))
    decision: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    representative: Mapped[bool] = mapped_column(Boolean, default=False)
    cluster_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(80), nullable=True)
    search_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpc: Mapped[float | None] = mapped_column(Float, nullable=True)
    granularity: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ranking_score: Mapped[float | None] = mapped_column(Float, nullable=True)


class JsonArtifactORM(TimestampMixin, Base):
    __tablename__ = "json_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class KeywordMetricORM(TimestampMixin, Base):
    __tablename__ = "keyword_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    keyword: Mapped[str] = mapped_column(String(240))
    canonical_keyword: Mapped[str] = mapped_column(String(240))
    intent: Mapped[str] = mapped_column(String(80))
    search_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpc: Mapped[float | None] = mapped_column(Float, nullable=True)
    paid_competition: Mapped[float | None] = mapped_column(Float, nullable=True)
    monthly_history: Mapped[list[int]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(120))
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    market_granularity: Mapped[str] = mapped_column(String(40))
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    excluded_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class SerpSnapshotORM(TimestampMixin, Base):
    __tablename__ = "serp_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    query: Mapped[str] = mapped_column(String(240))
    market_id: Mapped[str] = mapped_column(String(160))
    device: Mapped[str] = mapped_column(String(40))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    features_present: Mapped[list[str]] = mapped_column(JSON, default=list)
    raw_response_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)


class SerpResultORM(TimestampMixin, Base):
    __tablename__ = "serp_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    serp_snapshot_id: Mapped[int] = mapped_column(ForeignKey("serp_snapshots.id"))
    order: Mapped[int] = mapped_column(Integer)
    result_type: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(240))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    classification: Mapped[str] = mapped_column(String(80))
    is_local_provider: Mapped[bool] = mapped_column(Boolean, default=False)
    is_directory: Mapped[bool] = mapped_column(Boolean, default=False)
    is_national_brand: Mapped[bool] = mapped_column(Boolean, default=False)
    is_lead_generation_site: Mapped[bool] = mapped_column(Boolean, default=False)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classifier_version: Mapped[str] = mapped_column(String(40), default="v2")
    matched_rules: Mapped[list[str]] = mapped_column(JSON, default=list)
    classification_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manual_override: Mapped[str | None] = mapped_column(String(80), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class CompetitorMetricORM(TimestampMixin, Base):
    __tablename__ = "competitor_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(240))
    referring_domains: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backlinks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    authority: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    local_relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_type: Mapped[str] = mapped_column(String(80))
    relevance_signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProviderCandidateORM(TimestampMixin, Base):
    __tablename__ = "provider_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(240))
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    email: Mapped[str | None] = mapped_column(String(240), nullable=True)
    contact_form_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_area: Mapped[str | None] = mapped_column(String(200), nullable=True)
    category: Mapped[str | None] = mapped_column(String(160), nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    business_status: Mapped[str] = mapped_column(String(80))
    contact_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(120))
    raw_response_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    outreach_status: Mapped[str] = mapped_column(String(80))
    suitability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    suitability_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)


class PreliminaryAssessmentORM(TimestampMixin, Base):
    __tablename__ = "preliminary_assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    scoring_version: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[str] = mapped_column(String(20), default="preliminary")
    missing_components: Mapped[list[str]] = mapped_column(JSON, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class FullOpportunityScoreORM(TimestampMixin, Base):
    __tablename__ = "full_opportunity_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    scoring_version: Mapped[str] = mapped_column(String(40))
    total_score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(20))
    explanation: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ScoreComponentORM(TimestampMixin, Base):
    __tablename__ = "score_components"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    component: Mapped[str] = mapped_column(String(120))
    score: Mapped[float] = mapped_column(Float)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    formula: Mapped[str] = mapped_column(Text, default="")
    penalties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DomainCandidateORM(TimestampMixin, Base):
    __tablename__ = "domain_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    domain: Mapped[str] = mapped_column(String(240))
    availability_status: Mapped[str] = mapped_column(String(80))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class OutreachDraftORM(TimestampMixin, Base):
    __tablename__ = "outreach_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    provider_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_candidates.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(80), default="draft")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SiteConfigORM(TimestampMixin, Base):
    __tablename__ = "site_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    version: Mapped[str] = mapped_column(String(40), default="v1")
    status: Mapped[str] = mapped_column(String(80), default="draft")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AssetORM(TimestampMixin, Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    site_config_id: Mapped[int | None] = mapped_column(ForeignKey("site_configs.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(80))
    source_provider: Mapped[str] = mapped_column(String(120), default="manual")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DeploymentORM(TimestampMixin, Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_config_id: Mapped[int] = mapped_column(ForeignKey("site_configs.id"))
    provider: Mapped[str] = mapped_column(String(120))
    environment: Mapped[str] = mapped_column(String(80), default="staging")
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(80), default="created")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ProviderConfigORM(TimestampMixin, Base):
    __tablename__ = "provider_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    provider_candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    routing_notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=False)


class InterventionLogORM(TimestampMixin, Base):
    __tablename__ = "intervention_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    lifecycle_stage: Mapped[str] = mapped_column(String(80))
    action_type: Mapped[str] = mapped_column(String(80))
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text)
    recurs_for_every_property: Mapped[bool] = mapped_column(default=True)
    suggested_future_automation: Mapped[str] = mapped_column(Text, default="")
