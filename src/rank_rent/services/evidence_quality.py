from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from rank_rent.domain.models import (
    CompetitorMetric,
    Confidence,
    KeywordMetric,
    OpportunityScore,
    ProviderCandidate,
    SerpSnapshot,
    ServiceFamily,
    slugify,
)


class EvidenceQualityConfig(BaseModel):
    version: str
    minimum_keyword_service_relevance_share: float = Field(ge=0, le=1)
    minimum_representative_query_relevance_share: float = Field(ge=0, le=1)
    minimum_provider_service_relevance_share: float = Field(ge=0, le=1)
    minimum_provider_geographic_relevance_share: float = Field(ge=0, le=1)
    minimum_full_competitor_count: int = Field(ge=1)
    maximum_unknown_serp_share: float = Field(ge=0, le=1)
    unusable_score_cap: float = Field(ge=0, le=100)
    generic_service_tokens: list[str] = Field(default_factory=list)


class EvidenceQualityIssue(BaseModel):
    code: str
    stage: str
    severity: Literal["warning", "error"]
    message: str
    observed: float | int | str | None = None
    required: float | int | str | None = None


class EvidenceQualityAssessment(BaseModel):
    version: str
    config_hash: str
    status: Literal["pass", "warning", "fail"]
    service_relevance_tokens: list[str]
    measurements: dict[str, Any] = Field(default_factory=dict)
    issues: list[EvidenceQualityIssue] = Field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.status != "fail"


class EvidenceQualityEvaluator:
    def __init__(
        self,
        config_path: Path = Path("config/evidence_quality.yaml"),
    ) -> None:
        raw = config_path.read_text()
        self.config = EvidenceQualityConfig.model_validate(yaml.safe_load(raw))
        self.config_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def assess(
        self,
        *,
        service: ServiceFamily,
        metrics: list[KeywordMetric],
        serp_snapshots: list[SerpSnapshot],
        competitors: list[CompetitorMetric],
        providers: list[ProviderCandidate],
        assessment_type: str,
        service_configured: bool = True,
    ) -> EvidenceQualityAssessment:
        tokens = _service_tokens(service, set(self.config.generic_service_tokens))
        issues: list[EvidenceQualityIssue] = []
        metric_share = _text_relevance_share(
            [metric.keyword for metric in metrics if metric.included],
            tokens,
        )
        query_share = _text_relevance_share(
            [snapshot.query for snapshot in serp_snapshots],
            tokens,
        )
        provider_service_share = _provider_signal_share(
            providers,
            "service_fit",
            minimum=0.35,
        )
        provider_geography_share = _provider_signal_share(
            providers,
            "geographic_fit",
            minimum=0.50,
        )
        organic_results = [
            result
            for snapshot in serp_snapshots
            for result in snapshot.results
            if result.result_type == "organic"
        ]
        unknown_share = (
            sum(result.classification == "unknown" for result in organic_results)
            / len(organic_results)
            if organic_results
            else 1.0
        )

        _minimum_share_issue(
            issues,
            code="keyword_service_relevance",
            stage="keyword_metrics",
            observed=metric_share,
            required=self.config.minimum_keyword_service_relevance_share,
            noun="included keyword metrics",
            severity="error",
        )
        _minimum_share_issue(
            issues,
            code="representative_query_relevance",
            stage="serp",
            observed=query_share,
            required=self.config.minimum_representative_query_relevance_share,
            noun="representative SERP queries",
            severity="error",
        )
        if providers:
            _minimum_share_issue(
                issues,
                code="provider_service_relevance",
                stage="provider_discovery",
                observed=provider_service_share,
                required=self.config.minimum_provider_service_relevance_share,
                noun="provider listings with credible service fit",
                severity="error",
            )
            _minimum_share_issue(
                issues,
                code="provider_geographic_relevance",
                stage="provider_discovery",
                observed=provider_geography_share,
                required=self.config.minimum_provider_geographic_relevance_share,
                noun="provider listings with credible geographic fit",
                severity="warning",
            )
        if unknown_share > self.config.maximum_unknown_serp_share:
            issues.append(
                EvidenceQualityIssue(
                    code="serp_classification_coverage",
                    stage="serp",
                    severity="warning",
                    message=(
                        f"{unknown_share:.0%} of organic results remain unclassified; "
                        "organic-click confidence is limited."
                    ),
                    observed=round(unknown_share, 4),
                    required=self.config.maximum_unknown_serp_share,
                )
            )
        if (
            assessment_type == "full"
            and len(competitors) < self.config.minimum_full_competitor_count
        ):
            issues.append(
                EvidenceQualityIssue(
                    code="competitor_sample_coverage",
                    stage="competitors",
                    severity="error",
                    message=(
                        f"A full assessment has {len(competitors)} enriched competitors; "
                        f"at least {self.config.minimum_full_competitor_count} are required."
                    ),
                    observed=len(competitors),
                    required=self.config.minimum_full_competitor_count,
                )
            )
        if assessment_type == "full" and not service_configured:
            issues.append(
                EvidenceQualityIssue(
                    code="unconfigured_service",
                    stage="service_resolution",
                    severity="error",
                    message=(
                        "Full assessments require an authoritative configured service "
                        "definition with keyword and provider-category rules."
                    ),
                    observed="draft",
                    required="configured",
                )
            )
        status: Literal["pass", "warning", "fail"] = (
            "fail"
            if any(issue.severity == "error" for issue in issues)
            else "warning"
            if issues
            else "pass"
        )
        return EvidenceQualityAssessment(
            version=self.config.version,
            config_hash=self.config_hash,
            status=status,
            service_relevance_tokens=sorted(tokens),
            measurements={
                "keyword_service_relevance_share": round(metric_share, 4),
                "representative_query_relevance_share": round(query_share, 4),
                "provider_service_relevance_share": round(provider_service_share, 4),
                "provider_geographic_relevance_share": round(
                    provider_geography_share,
                    4,
                ),
                "unknown_serp_share": round(unknown_share, 4),
                "competitor_count": len(competitors),
                "provider_count": len(providers),
                "organic_result_count": len(organic_results),
                "service_configured": service_configured,
            },
            issues=issues,
        )

    def apply_to_score(
        self,
        score: OpportunityScore,
        assessment: EvidenceQualityAssessment,
    ) -> OpportunityScore:
        if assessment.status == "pass":
            return score
        assumptions = [
            *score.assumptions,
            *[issue.message for issue in assessment.issues],
        ]
        measurements = {
            **score.input_measurements,
            "evidence_quality": assessment.model_dump(mode="json"),
        }
        if assessment.status == "warning":
            confidence = (
                Confidence.medium
                if score.confidence == Confidence.high
                else score.confidence
            )
            return score.model_copy(
                update={
                    "confidence": confidence,
                    "assumptions": assumptions,
                    "input_measurements": measurements,
                }
            )

        cap = self.config.unusable_score_cap
        return score.model_copy(
            update={
                "total_score": round(min(score.total_score, cap), 2),
                "score_cap": min(score.score_cap or cap, cap),
                "evidence_status": "unusable",
                "confidence": Confidence.insufficient,
                "explanation": (
                    f"Evidence-quality validation failed, so this assessment is unusable "
                    f"for ranking and capped at {cap:g}. {score.explanation}"
                ),
                "assumptions": assumptions,
                "input_measurements": measurements,
            }
        )


def _service_tokens(
    service: ServiceFamily,
    generic_tokens: set[str],
) -> set[str]:
    values = [
        service.display_name,
        service.description,
        *service.seed_queries,
        *service.provider_categories,
    ]
    tokens = {
        token
        for value in values
        for token in slugify(value).replace("-", " ").split()
        if len(token) >= 3 and token not in generic_tokens
    }
    if tokens:
        return tokens
    return {
        token
        for token in slugify(service.display_name).replace("-", " ").split()
        if len(token) >= 3
    }


def _text_relevance_share(values: list[str], tokens: set[str]) -> float:
    if not values or not tokens:
        return 0.0
    relevant = 0
    for value in values:
        value_tokens = set(slugify(value).replace("-", " ").split())
        relevant += bool(value_tokens & tokens)
    return relevant / len(values)


def _provider_signal_share(
    providers: list[ProviderCandidate],
    signal: str,
    *,
    minimum: float,
) -> float:
    if not providers:
        return 0.0
    relevant = 0
    for provider in providers:
        payload = provider.suitability_signals.get(signal, {})
        normalized = payload.get("normalized") if isinstance(payload, dict) else None
        relevant += isinstance(normalized, (int, float)) and normalized >= minimum
    return relevant / len(providers)


def _minimum_share_issue(
    issues: list[EvidenceQualityIssue],
    *,
    code: str,
    stage: str,
    observed: float,
    required: float,
    noun: str,
    severity: Literal["warning", "error"],
) -> None:
    if observed >= required:
        return
    issues.append(
        EvidenceQualityIssue(
            code=code,
            stage=stage,
            severity=severity,
            message=(
                f"Only {observed:.0%} of {noun} match the configured service; "
                f"at least {required:.0%} are required."
            ),
            observed=round(observed, 4),
            required=required,
        )
    )
