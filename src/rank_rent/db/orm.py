from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship
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
    county: Mapped[str | None] = mapped_column(String(160), nullable=True)
    county_fips: Mapped[str | None] = mapped_column(String(5), nullable=True)
    metro: Mapped[str | None] = mapped_column(String(200), nullable=True)
    metro_code: Mapped[str | None] = mapped_column(String(5), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    population: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_population: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    boundary_radius_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    geography_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    geography_dataset_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
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


class MarketPrefilterRunORM(TimestampMixin, Base):
    __tablename__ = "market_prefilter_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_text: Mapped[str] = mapped_column(String(240))
    service_profile: Mapped[str] = mapped_column(String(80))
    geography_kind: Mapped[str] = mapped_column(String(40), default="city")
    state_filters: Mapped[list[str]] = mapped_column(JSON, default=list)
    minimum_population: Mapped[int] = mapped_column(Integer)
    candidate_count: Mapped[int] = mapped_column(Integer)
    returned_count: Mapped[int] = mapped_column(Integer)
    assessment_version: Mapped[str] = mapped_column(String(40))
    config_hash: Mapped[str] = mapped_column(String(40))
    geography_dataset_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="completed")


class MarketPrefilterAssessmentORM(TimestampMixin, Base):
    __tablename__ = "market_prefilter_assessments"
    __table_args__ = (
        UniqueConstraint(
            "prefilter_run_id",
            "geography_id",
            name="uq_market_prefilter_run_geography",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    prefilter_run_id: Mapped[int] = mapped_column(ForeignKey("market_prefilter_runs.id"))
    geography_id: Mapped[str] = mapped_column(String(80), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    recommendation: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ScanRunORM(TimestampMixin, Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True
    )
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
    source_scan_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_runs.id"), nullable=True
    )
    progress_stage: Mapped[str] = mapped_column(String(80), default="queued")
    partial_outputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quarantine_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScanPlanCallORM(TimestampMixin, Base):
    __tablename__ = "scan_plan_calls"
    __table_args__ = (
        UniqueConstraint(
            "scan_run_id",
            "planned_request_id",
            name="uq_scan_plan_calls_scan_planned_request",
        ),
    )

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
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_raw_api_responses_object_key"),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_raw_api_responses_size_bytes_nonnegative",
        ),
    )

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
    source_scan_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_runs.id"), nullable=True
    )
    checksum: Mapped[str] = mapped_column(String(128), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    storage_backend: Mapped[str | None] = mapped_column(String(40), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    retention_classification: Mapped[str] = mapped_column(
        String(80), default="raw_provider_response"
    )
    encryption_status: Mapped[str] = mapped_column(String(80), default="not_encrypted")
    blob_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ImmutableRawResponseError(RuntimeError):
    pass


_IMMUTABLE_RAW_RESPONSE_FIELDS = {
    "cache_key",
    "provider",
    "endpoint",
    "parameters",
    "api_version",
    "response_shape_version",
    "response_json",
    "sanitized",
    "status_code",
    "request_time",
    "response_time",
    "cost_usd",
    "provider_task_id",
    "provider_request_id",
    "source_scan_run_id",
    "checksum",
    "object_key",
    "storage_backend",
    "content_type",
    "size_bytes",
    "retention_classification",
    "encryption_status",
    "blob_created_at",
}


def _reject_raw_response_mutation(
    _mapper: Mapper[RawApiResponseORM],
    _connection: Connection,
    target: RawApiResponseORM,
) -> None:
    state = inspect(target)
    changed = sorted(
        field for field in _IMMUTABLE_RAW_RESPONSE_FIELDS if state.attrs[field].history.has_changes()
    )
    if changed:
        raise ImmutableRawResponseError(
            "Raw response content and lineage are immutable after insert: " + ", ".join(changed)
        )


event.listen(RawApiResponseORM, "before_update", _reject_raw_response_mutation)


class ApiCallORM(TimestampMixin, Base):
    __tablename__ = "api_calls"
    __table_args__ = (
        UniqueConstraint(
            "scan_run_id",
            "planned_request_id",
            name="uq_api_calls_scan_planned_request",
        ),
    )

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


class ProviderDailyUsageORM(TimestampMixin, Base):
    __tablename__ = "provider_daily_usage"
    __table_args__ = (
        UniqueConstraint(
            "usage_date",
            "usage_class",
            "provider",
            "endpoint",
            name="uq_provider_daily_usage_bucket",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    usage_date: Mapped[date] = mapped_column(index=True)
    usage_class: Mapped[str] = mapped_column(String(20), index=True)
    provider: Mapped[str] = mapped_column(String(80), index=True)
    endpoint: Mapped[str] = mapped_column(String(160), default="", index=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    spend_usd: Mapped[float] = mapped_column(Float, default=0)
    reserved_spend_usd: Mapped[float] = mapped_column(Float, default=0)
    cache_miss_count: Mapped[int] = mapped_column(Integer, default=0)
    unexpected_call_count: Mapped[int] = mapped_column(Integer, default=0)
    abnormal_cost_count: Mapped[int] = mapped_column(Integer, default=0)
    provider_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    schema_drift_count: Mapped[int] = mapped_column(Integer, default=0)


class ProviderQualificationORM(TimestampMixin, Base):
    __tablename__ = "provider_qualifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), index=True)
    environment: Mapped[str] = mapped_column(String(20), index=True)
    adapter_version: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    qualified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    checks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")


class BillingReconciliationORM(TimestampMixin, Base):
    __tablename__ = "billing_reconciliations"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), index=True)
    environment: Mapped[str] = mapped_column(String(20), index=True)
    period_start: Mapped[date] = mapped_column(index=True)
    period_end: Mapped[date] = mapped_column(index=True)
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    internal_call_count: Mapped[int] = mapped_column(Integer, default=0)
    provider_call_count: Mapped[int] = mapped_column(Integer, default=0)
    internal_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    provider_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    unmatched_provider_charges: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    unmatched_internal_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    difference_usd: Mapped[float] = mapped_column(Float, default=0)
    source_filename: Mapped[str] = mapped_column(String(240), default="")


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
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class KeywordMetricORM(TimestampMixin, Base):
    __tablename__ = "keyword_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True
    )
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
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True
    )
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
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True
    )
    url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(240))
    page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_domain: Mapped[str | None] = mapped_column(String(240), nullable=True)
    referring_domains: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backlinks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    authority: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_referring_domains: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_backlinks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_authority: Mapped[float | None] = mapped_column(Float, nullable=True)
    domain_referring_domains: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domain_backlinks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domain_authority: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_metrics_available: Mapped[bool] = mapped_column(Boolean, default=False)
    domain_metrics_available: Mapped[bool] = mapped_column(Boolean, default=False)
    page_relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    local_relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_type: Mapped[str] = mapped_column(String(80))
    relevance_signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    representative_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    serp_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    serp_observations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    serp_observation_records: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProviderCandidateORM(TimestampMixin, Base):
    __tablename__ = "provider_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"))
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(240))
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    email: Mapped[str | None] = mapped_column(String(240), nullable=True)
    contact_form_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_area: Mapped[str | None] = mapped_column(String(200), nullable=True)
    category: Mapped[str | None] = mapped_column(String(160), nullable=True)
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    business_status: Mapped[str] = mapped_column(String(80))
    contact_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(120))
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    raw_response_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    outreach_status: Mapped[str] = mapped_column(String(80))
    suitability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    suitability_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    suitability_signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


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
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True
    )
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
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True
    )
    lifecycle_stage: Mapped[str] = mapped_column(String(80))
    action_type: Mapped[str] = mapped_column(String(80))
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text)
    recurs_for_every_property: Mapped[bool] = mapped_column(default=True)
    suggested_future_automation: Mapped[str] = mapped_column(Text, default="")
