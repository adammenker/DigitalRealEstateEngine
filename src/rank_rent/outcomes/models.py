from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from rank_rent.lead_routing.models import TruthBasis


class OutcomeSourceType(StrEnum):
    search_console = "search_console"
    web_analytics = "web_analytics"
    call_tracking = "call_tracking"
    form_submission = "form_submission"
    provider = "provider"
    operator = "operator"


class PropertyDecisionInput(BaseModel):
    property_id: str = Field(min_length=1, max_length=120)
    opportunity_id: int = Field(gt=0)
    full_score_id: int = Field(gt=0)
    evidence_snapshot_id: int = Field(gt=0)
    selected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    service_family_slug: str = Field(min_length=1, max_length=120)
    market_size_band: str = Field(min_length=1, max_length=80)
    evidence_quality: str = Field(min_length=1, max_length=40)
    validated_opportunity_cost_usd: float = Field(default=0, ge=0)


class PropertyOutcomeRecord(BaseModel):
    property_id: str = Field(min_length=1, max_length=120)
    period_date: date
    source_type: OutcomeSourceType
    source_name: str = Field(min_length=1, max_length=120)
    source_record_id: str = Field(min_length=1, max_length=160)
    truth_basis: TruthBasis
    confidence: str = Field(pattern=r"^(high|medium|low|insufficient)$")
    impressions: int = Field(default=0, ge=0)
    clicks: int = Field(default=0, ge=0)
    average_position: float | None = Field(default=None, ge=0)
    organic_sessions: int = Field(default=0, ge=0)
    calls: int = Field(default=0, ge=0)
    forms: int = Field(default=0, ge=0)
    qualified_leads: int = Field(default=0, ge=0)
    appointments: int = Field(default=0, ge=0)
    won_jobs: int = Field(default=0, ge=0)
    reported_revenue: float = Field(default=0, ge=0)
    indexed_at: datetime | None = None
    provider_suitability_score: float | None = Field(default=None, ge=0, le=100)
    addressable_market_score: float | None = Field(default=None, ge=0, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source_truth(self) -> PropertyOutcomeRecord:
        if self.truth_basis == TruthBasis.provider_reported:
            if self.source_type != OutcomeSourceType.provider:
                raise ValueError("Provider-reported outcomes must use the provider source.")
        if self.truth_basis == TruthBasis.operator_verified:
            if self.source_type != OutcomeSourceType.operator:
                raise ValueError("Operator-verified outcomes must use the operator source.")
        if self.reported_revenue > 0 and self.truth_basis == TruthBasis.estimated:
            raise ValueError("Estimated revenue cannot be stored as reported revenue.")
        return self


class CalibrationReportRequest(BaseModel):
    start_date: date
    end_date: date
    minimum_correlation_sample: int = Field(default=5, ge=3, le=1000)

    @model_validator(mode="after")
    def validate_range(self) -> CalibrationReportRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date.")
        return self


class CorrelationResult(BaseModel):
    metric: str
    sample_size: int
    coefficient: float | None
    sufficient_sample: bool
    interpretation: str


class CalibrationReport(BaseModel):
    report_id: int | None = None
    report_version: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    start_date: date
    end_date: date
    property_count: int
    observed_totals: dict[str, float]
    reported_totals: dict[str, float]
    operator_verified_totals: dict[str, float]
    estimated_totals: dict[str, float]
    correlations: list[CorrelationResult]
    segment_summaries: dict[str, list[dict[str, Any]]]
    cost_per_validated_opportunity_usd: float | None
    warnings: list[str]
    scoring_changes_applied: bool = False


class ScoringChangeProposal(BaseModel):
    proposal_id: str = Field(min_length=1, max_length=120)
    current_version: str = Field(min_length=1, max_length=80)
    proposed_version: str = Field(min_length=1, max_length=80)
    initiated_by: str = Field(min_length=1, max_length=120)
    reviewer_id: str | None = Field(default=None, max_length=120)
    benchmark_run_id: str | None = Field(default=None, max_length=120)
    benchmark_passed: bool = False
    rationale: str = Field(min_length=10, max_length=2000)


class ScoringChangeAuthorization(BaseModel):
    proposal_id: str
    authorized_for_manual_application: bool
    reviewer_id: str
    benchmark_run_id: str
    note: str
