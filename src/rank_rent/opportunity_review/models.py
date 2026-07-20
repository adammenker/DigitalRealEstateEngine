from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class OpportunityState(StrEnum):
    discovered = "discovered"
    prefilter_review = "prefilter_review"
    testing_planned = "testing_planned"
    testing_running = "testing_running"
    preliminary_review = "preliminary_review"
    full_scan_approved = "full_scan_approved"
    full_running = "full_running"
    full_review = "full_review"
    needs_more_evidence = "needs_more_evidence"
    approved_for_property = "approved_for_property"
    rejected = "rejected"
    archived = "archived"


class ReviewRole(StrEnum):
    operator = "operator"
    reviewer = "reviewer"
    admin = "admin"
    system = "system"


class ReviewActor(BaseModel):
    actor_id: str = Field(min_length=1, max_length=120)
    role: ReviewRole


class ReviewTransitionRequest(BaseModel):
    target_state: OpportunityState
    decision: str = Field(min_length=2, max_length=80)
    decision_reason: str = Field(min_length=5, max_length=2000)
    notes: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    owner_user_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected_review_version: int | None = Field(default=None, ge=0)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        return sorted({" ".join(value.split()).lower() for value in values if value.strip()})


class OwnershipRequest(BaseModel):
    owner_user_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=5, max_length=1000)
    expected_review_version: int | None = Field(default=None, ge=0)


class EvidenceOverrideKind(StrEnum):
    serp_classification = "serp_classification"
    provider_suitability = "provider_suitability"
    geographic_interpretation = "geographic_interpretation"
    data_quality_warning = "data_quality_warning"


class EvidenceOverrideRequest(BaseModel):
    override_kind: EvidenceOverrideKind
    target_record_id: int = Field(gt=0)
    field_name: str = Field(min_length=1, max_length=120)
    new_value: Any
    expected_original_value: Any | None = None
    reason: str = Field(min_length=10, max_length=2000)
    score_impact: float = Field(ge=-100, le=100)
    score_impact_explanation: str = Field(min_length=5, max_length=1000)


class EvidenceOverrideReversalRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)
    score_impact: float = Field(ge=-100, le=100)
    score_impact_explanation: str = Field(min_length=5, max_length=1000)


class DiscoveryTemplateInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    service_family_id: int = Field(gt=0)
    market_filters: dict[str, Any] = Field(default_factory=dict)
    prefilter_profile: str = Field(min_length=1, max_length=80)
    testing_profile: str = Field(default="testing", pattern=r"^testing$")
    full_profile: str = Field(default="full", pattern=r"^full$")
    budget_usd: Decimal = Field(ge=0, max_digits=12, decimal_places=4)
    freshness_requirements: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.split())


class BatchPlanRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    opportunity_ids: list[int] = Field(min_length=1, max_length=100)
    scan_profile: Literal["testing", "full"] = "testing"
    data_mode: Literal["fixture", "replay", "live"] = "fixture"
    aggregate_budget_usd: Decimal = Field(ge=0, max_digits=12, decimal_places=4)
    template_id: int | None = Field(default=None, gt=0)

    @field_validator("opportunity_ids")
    @classmethod
    def unique_opportunities(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("opportunity_ids must contain positive integers.")
        deduplicated = list(dict.fromkeys(values))
        if len(deduplicated) != len(values):
            raise ValueError("opportunity_ids must not contain duplicates.")
        return deduplicated


class BatchConfirmationRequest(BaseModel):
    approved_max_cost_usd: Decimal = Field(ge=0, max_digits=12, decimal_places=4)
    reason: str = Field(min_length=10, max_length=2000)


class ApprovalCompleteness(BaseModel):
    complete: bool
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    full_scan_run_id: int | None = None
    full_score_id: int | None = None
    checks: dict[str, bool]
    failures: list[str]
    warnings: list[str]


class EvidencePacketFormat(StrEnum):
    json = "json"
    csv = "csv"


class EvidencePacket(BaseModel):
    packet_version: str = "opportunity-evidence-v1"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    generated_by: str
    opportunity: dict[str, Any]
    review: dict[str, Any]
    market_evidence: dict[str, Any]
    keyword_decisions: list[dict[str, Any]]
    serps: list[dict[str, Any]]
    competitors: list[dict[str, Any]]
    providers: list[dict[str, Any]]
    score_trace: dict[str, Any]
    confidence: str | None
    costs: dict[str, Any]
    freshness: dict[str, Any]
    overrides: list[dict[str, Any]]
    review_notes: list[dict[str, Any]]


class ReviewStateErrorDetail(BaseModel):
    current_state: str
    requested_state: str
    allowed_states: list[str]


class BatchQueueResult(BaseModel):
    batch_plan_id: int
    queued_scan_ids: list[int]
    aggregate_planned_cost_usd: Decimal
    approved_max_cost_usd: Decimal

    @model_validator(mode="after")
    def ensure_cost_bound(self) -> BatchQueueResult:
        if self.aggregate_planned_cost_usd > self.approved_max_cost_usd:
            raise ValueError("Queued plan exceeds its approved aggregate cost.")
        return self
