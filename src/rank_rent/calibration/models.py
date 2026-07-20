from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rank_rent.domain.models import (
    CompetitorMetric,
    KeywordMetric,
    Market,
    ProviderCandidate,
    SerpResult,
    SerpSnapshot,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FreshnessSpec(StrictModel):
    keyword_age_days: int = Field(default=1, ge=0)
    serp_age_days: int = Field(default=1, ge=0)
    competitor_age_days: int = Field(default=1, ge=0)
    provider_age_days: int = Field(default=1, ge=0)


class BenchmarkEvidence(StrictModel):
    keyword_metrics: list[KeywordMetric] = Field(default_factory=list)
    serp_snapshots: list[SerpSnapshot] = Field(default_factory=list)
    competitors: list[CompetitorMetric] = Field(default_factory=list)
    providers: list[ProviderCandidate] = Field(default_factory=list)
    demand: dict[str, Any] = Field(default_factory=dict)
    freshness: FreshnessSpec = Field(default_factory=FreshnessSpec)


def _default_quality_statuses() -> list[Literal["pass", "warning", "fail"]]:
    return ["pass", "warning"]


class ScenarioExpectations(StrictModel):
    rankable: bool
    assessment_type: Literal["full", "preliminary"]
    confidence_in: list[Literal["insufficient", "low", "medium", "high"]]
    quality_status_in: list[Literal["pass", "warning", "fail"]] = Field(
        default_factory=_default_quality_statuses
    )
    evidence_status_in: list[str] = Field(default_factory=list)
    score_range: tuple[float, float] = (0, 100)
    component_ranges: dict[str, tuple[float, float]] = Field(default_factory=dict)
    invariants: list[str] = Field(
        default_factory=lambda: [
            "score_between_0_and_100",
            "components_within_weights",
            "config_identity_matches",
        ]
    )

    @model_validator(mode="after")
    def ranges_are_ordered(self) -> Self:
        ranges = {"score": self.score_range, **self.component_ranges}
        for name, (minimum, maximum) in ranges.items():
            if minimum > maximum:
                raise ValueError(f"{name} range minimum exceeds its maximum")
        return self


class BenchmarkScenario(StrictModel):
    scenario_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1)
    description: str = Field(min_length=1)
    service_family_id: str
    market: Market
    data_mode: Literal["benchmark"] = "benchmark"
    assessment_type: Literal["full", "preliminary"] = "full"
    service_configured: bool = True
    evidence: BenchmarkEvidence
    expectations: ScenarioExpectations


class PairwiseExpectation(StrictModel):
    preferred: str
    over: str
    reason: str = Field(min_length=1)
    component: str | None = None
    minimum_margin: float = Field(default=0, ge=0)


class ScenarioLibrary(StrictModel):
    schema_version: int = Field(ge=1)
    suite_version: str
    fixtures: dict[str, Any] = Field(default_factory=dict)
    scenarios: list[BenchmarkScenario]
    pairwise_expectations: list[PairwiseExpectation]

    @model_validator(mode="after")
    def ids_and_references_are_valid(self) -> Self:
        ids = [scenario.scenario_id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario_id values must be unique")
        known = set(ids)
        for expectation in self.pairwise_expectations:
            missing = {expectation.preferred, expectation.over} - known
            if missing:
                raise ValueError(
                    f"pairwise expectation references unknown scenarios: {sorted(missing)}"
                )
            if expectation.preferred == expectation.over:
                raise ValueError("pairwise expectation cannot compare a scenario to itself")
        return self


class ClassificationCase(StrictModel):
    case_id: str
    service_family_id: str = "water_heater_services"
    market: Market
    result: SerpResult
    providers: list[ProviderCandidate] = Field(default_factory=list)
    expected_classification: str
    confidence_range: tuple[float, float]


class ClassificationLibrary(StrictModel):
    schema_version: int = Field(ge=1)
    version: str
    cases: list[ClassificationCase]

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> Self:
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("classification case_id values must be unique")
        return self


class ProviderCase(StrictModel):
    case_id: str
    service_family_id: str = "water_heater_services"
    market: Market
    provider: ProviderCandidate
    expected_score_range: tuple[float, float]
    expected_suitable: bool


class ProviderPairwiseExpectation(StrictModel):
    preferred: str
    over: str
    reason: str


class ProviderLibrary(StrictModel):
    schema_version: int = Field(ge=1)
    version: str
    cases: list[ProviderCase]
    pairwise_expectations: list[ProviderPairwiseExpectation] = Field(default_factory=list)

    @model_validator(mode="after")
    def ids_and_references_are_valid(self) -> Self:
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("provider case_id values must be unique")
        known = set(ids)
        for expectation in self.pairwise_expectations:
            missing = {expectation.preferred, expectation.over} - known
            if missing:
                raise ValueError(
                    f"provider pairwise expectation references unknown cases: {sorted(missing)}"
                )
            if expectation.preferred == expectation.over:
                raise ValueError(
                    "provider pairwise expectation cannot compare a case to itself"
                )
        return self


class BenchmarkManifest(StrictModel):
    schema_version: int = Field(ge=1)
    suite_version: str
    minimum_scenario_count: int = Field(ge=20)
    default_scoring_version: str
    active_scoring_config: str
    scoring_configs: dict[str, str]
    scenario_library: str
    classification_library: str
    provider_library: str
    service_catalog: str
    evidence_quality_config: str
    serp_classification_config: str
    reports_directory: str = "benchmarks/reports"

    @model_validator(mode="after")
    def default_scoring_version_is_registered(self) -> Self:
        if self.default_scoring_version not in self.scoring_configs:
            raise ValueError(
                "default_scoring_version must be registered in scoring_configs"
            )
        return self


class CheckResult(StrictModel):
    check: str
    passed: bool
    expected: Any = None
    actual: Any = None
    message: str = ""


class ScenarioResult(StrictModel):
    scenario_id: str
    scenario_version: int
    passed: bool
    rankable: bool
    assessment_type: str
    score: float
    confidence: str
    evidence_status: str
    evidence_quality_status: str
    component_scores: dict[str, float]
    checks: list[CheckResult]


class PairwiseResult(StrictModel):
    preferred: str
    over: str
    reason: str
    component: str | None
    minimum_margin: float
    preferred_value: float
    other_value: float
    passed: bool


class BenchmarkSummary(StrictModel):
    total: int
    passed: int
    failed: int


class CalibrationReport(StrictModel):
    report_schema_version: int = 1
    report_id: str
    created_at: datetime
    suite_version: str
    scoring_version: str
    scoring_config_hash: str
    benchmark_config_hash: str
    success: bool
    network_attempt_count: int
    scenario_summary: BenchmarkSummary
    scenario_results: list[ScenarioResult]
    pairwise_summary: BenchmarkSummary
    pairwise_results: list[PairwiseResult]
    component_distributions: dict[str, dict[str, float]]
    evidence_gate_confusion: dict[str, int]
    classification_summary: dict[str, Any]
    provider_summary: dict[str, Any]


class ScoreVersionDiff(StrictModel):
    scenario_id: str
    score_a: float
    score_b: float
    score_delta: float
    rankable_a: bool
    rankable_b: bool
    component_deltas: dict[str, float]


class ComparisonReport(StrictModel):
    report_schema_version: int = 1
    created_at: datetime
    suite_version: str
    version_a: str
    config_hash_a: str
    version_b: str
    config_hash_b: str
    benchmark_config_hash: str
    network_attempt_count: int
    scenario_diffs: list[ScoreVersionDiff]
    pairwise_regressions: list[str]


class ValidationReport(StrictModel):
    valid: bool
    suite_version: str
    benchmark_config_hash: str
    scenario_count: int
    scoring_versions: list[str]
    checks: list[CheckResult]
