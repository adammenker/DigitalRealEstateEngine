from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any

from rank_rent.calibration.loader import (
    DEFAULT_MANIFEST,
    CalibrationConfigError,
    benchmark_config_hash,
    load_libraries,
    load_manifest,
    load_raw_yaml,
    project_path,
)
from rank_rent.calibration.models import (
    BenchmarkScenario,
    BenchmarkSummary,
    CalibrationReport,
    CheckResult,
    ComparisonReport,
    PairwiseResult,
    ScenarioResult,
    ScoreVersionDiff,
    ValidationReport,
)
from rank_rent.calibration.network import block_network
from rank_rent.domain.models import OpportunityScore, SerpSnapshot
from rank_rent.scoring.score import OpportunityScorer
from rank_rent.scoring.serp import classify_result
from rank_rent.services.competitors import enrich_competitors
from rank_rent.services.evidence_quality import EvidenceQualityEvaluator
from rank_rent.services.providers import score_provider_suitability
from rank_rent.services.service_catalog import ServiceCatalog

SUPPORTED_INVARIANTS = {
    "score_between_0_and_100",
    "components_within_weights",
    "config_identity_matches",
    "full_rankable_requires_quality",
    "preliminary_not_rankable",
}


class CalibrationRunner:
    def __init__(
        self,
        project_root: Path | None = None,
        manifest_path: Path = DEFAULT_MANIFEST,
    ) -> None:
        self.project_root = (project_root or Path.cwd()).resolve()
        self.manifest_path = manifest_path
        self.manifest = load_manifest(self.project_root, manifest_path)
        (
            self.scenario_library,
            self.classification_library,
            self.provider_library,
        ) = load_libraries(self.project_root, self.manifest)
        self.service_catalog = ServiceCatalog.from_path(
            project_path(self.project_root, self.manifest.service_catalog)
        )
        self.config_hash = benchmark_config_hash(
            self.project_root,
            self.manifest,
            self.manifest_path,
        )

    def run(self, scoring_version: str | None = None) -> CalibrationReport:
        with block_network() as network_guard:
            report = self._run(scoring_version or self.manifest.default_scoring_version)
        return report.model_copy(
            update={"network_attempt_count": network_guard.attempt_count}
        )

    def compare(self, version_a: str, version_b: str) -> ComparisonReport:
        with block_network() as network_guard:
            report_a = self._run(version_a)
            report_b = self._run(version_b)
        results_a = {item.scenario_id: item for item in report_a.scenario_results}
        results_b = {item.scenario_id: item for item in report_b.scenario_results}
        diffs = []
        for scenario_id in sorted(results_a):
            a = results_a[scenario_id]
            b = results_b[scenario_id]
            component_names = set(a.component_scores) | set(b.component_scores)
            diffs.append(
                ScoreVersionDiff(
                    scenario_id=scenario_id,
                    score_a=a.score,
                    score_b=b.score,
                    score_delta=round(b.score - a.score, 2),
                    rankable_a=a.rankable,
                    rankable_b=b.rankable,
                    component_deltas={
                        component: round(
                            b.component_scores.get(component, 0)
                            - a.component_scores.get(component, 0),
                            2,
                        )
                        for component in sorted(component_names)
                    },
                )
            )
        pairwise_a = {
            (item.preferred, item.over, item.component): item.passed
            for item in report_a.pairwise_results
        }
        pairwise_regressions = [
            f"{item.preferred}>{item.over}"
            for item in report_b.pairwise_results
            if pairwise_a.get((item.preferred, item.over, item.component)) and not item.passed
        ]
        return ComparisonReport(
            created_at=datetime.now(UTC),
            suite_version=self.manifest.suite_version,
            version_a=report_a.scoring_version,
            config_hash_a=report_a.scoring_config_hash,
            version_b=report_b.scoring_version,
            config_hash_b=report_b.scoring_config_hash,
            benchmark_config_hash=self.config_hash,
            network_attempt_count=network_guard.attempt_count,
            scenario_diffs=diffs,
            pairwise_regressions=pairwise_regressions,
        )

    def validate_config(self) -> ValidationReport:
        checks: list[CheckResult] = []
        checks.append(
            _check(
                "suite_version_matches_scenario_library",
                self.scenario_library.suite_version == self.manifest.suite_version,
                self.manifest.suite_version,
                self.scenario_library.suite_version,
            )
        )
        checks.append(
            _check(
                "minimum_scenario_count",
                len(self.scenario_library.scenarios)
                >= self.manifest.minimum_scenario_count,
                f">={self.manifest.minimum_scenario_count}",
                len(self.scenario_library.scenarios),
            )
        )
        invariants = {
            invariant
            for scenario in self.scenario_library.scenarios
            for invariant in scenario.expectations.invariants
        }
        unknown_invariants = sorted(invariants - SUPPORTED_INVARIANTS)
        checks.append(
            _check(
                "supported_invariants",
                not unknown_invariants,
                [],
                unknown_invariants,
            )
        )
        service_ids = {
            item.service.id
            for item in self.service_catalog.list_services(include_disabled=True)
        }
        referenced_services = {
            scenario.service_family_id
            for scenario in self.scenario_library.scenarios
        } | {
            case.service_family_id for case in self.classification_library.cases
        } | {
            case.service_family_id for case in self.provider_library.cases
        }
        unknown_services = sorted(referenced_services - service_ids)
        checks.append(
            _check("configured_service_references", not unknown_services, [], unknown_services)
        )
        for version, configured_path in sorted(self.manifest.scoring_configs.items()):
            config_path = project_path(self.project_root, configured_path)
            raw = load_raw_yaml(config_path)
            checks.extend(self._scoring_config_checks(version, config_path, raw))
        active_path = project_path(
            self.project_root,
            self.manifest.active_scoring_config,
        )
        default_snapshot_path = project_path(
            self.project_root,
            self.manifest.scoring_configs[self.manifest.default_scoring_version],
        )
        active_bytes = active_path.read_bytes()
        snapshot_bytes = default_snapshot_path.read_bytes()
        checks.append(
            _check(
                "active_scoring_config_matches_default_snapshot",
                active_bytes == snapshot_bytes,
                hashlib.sha256(snapshot_bytes).hexdigest(),
                hashlib.sha256(active_bytes).hexdigest(),
                (
                    "Increment the scoring version and archive a new snapshot before "
                    "changing production scoring."
                ),
            )
        )
        return ValidationReport(
            valid=all(check.passed for check in checks),
            suite_version=self.manifest.suite_version,
            benchmark_config_hash=self.config_hash,
            scenario_count=len(self.scenario_library.scenarios),
            scoring_versions=sorted(self.manifest.scoring_configs),
            checks=checks,
        )

    def _run(self, scoring_version: str) -> CalibrationReport:
        scorer = self._scorer(scoring_version)
        evidence_quality = EvidenceQualityEvaluator(
            project_path(self.project_root, self.manifest.evidence_quality_config)
        )
        scenario_results = [
            self._run_scenario(scenario, scorer, evidence_quality)
            for scenario in self.scenario_library.scenarios
        ]
        by_id = {result.scenario_id: result for result in scenario_results}
        pairwise_results = [
            self._pairwise_result(expectation, by_id)
            for expectation in self.scenario_library.pairwise_expectations
        ]
        classification_summary = self._classification_benchmark()
        provider_summary = self._provider_benchmark(scorer)
        scenario_summary = _summary([item.passed for item in scenario_results])
        pairwise_summary = _summary([item.passed for item in pairwise_results])
        success = (
            scenario_summary.failed == 0
            and pairwise_summary.failed == 0
            and classification_summary["failed"] == 0
            and provider_summary["failed"] == 0
        )
        report_id = hashlib.sha256(
            (
                f"{self.manifest.suite_version}:{scorer.config['version']}:"
                f"{scorer.config_hash}:{self.config_hash}"
            ).encode()
        ).hexdigest()[:12]
        return CalibrationReport(
            report_id=report_id,
            created_at=datetime.now(UTC),
            suite_version=self.manifest.suite_version,
            scoring_version=str(scorer.config["version"]),
            scoring_config_hash=scorer.config_hash,
            benchmark_config_hash=self.config_hash,
            success=success,
            network_attempt_count=0,
            scenario_summary=scenario_summary,
            scenario_results=scenario_results,
            pairwise_summary=pairwise_summary,
            pairwise_results=pairwise_results,
            component_distributions=_component_distributions(scenario_results),
            evidence_gate_confusion=_evidence_gate_confusion(scenario_results),
            classification_summary=classification_summary,
            provider_summary=provider_summary,
        )

    def _run_scenario(
        self,
        scenario: BenchmarkScenario,
        scorer: OpportunityScorer,
        evidence_quality: EvidenceQualityEvaluator,
    ) -> ScenarioResult:
        service = self._service(scenario.service_family_id)
        evidence = _fresh_evidence(scenario, datetime.now(UTC))
        providers = score_provider_suitability(
            evidence["providers"],
            service,
            scenario.market,
            scorer.config["providers"],
        )
        serp_snapshots = _classify_snapshots(
            evidence["serp_snapshots"],
            service=service,
            market=scenario.market,
            providers=providers,
        )
        competitors = enrich_competitors(
            evidence["competitors"],
            serp_snapshots,
            service,
            scenario.market,
        )
        quality = evidence_quality.assess(
            service=service,
            metrics=evidence["keyword_metrics"],
            serp_snapshots=serp_snapshots,
            competitors=competitors,
            providers=providers,
            assessment_type=scenario.assessment_type,
            service_configured=scenario.service_configured,
        )
        score = scorer.score(
            evidence["keyword_metrics"],
            serp_snapshots,
            competitors,
            providers,
            scenario.market,
            source_mode="benchmark",
            assessment_type=scenario.assessment_type,
        )
        score = evidence_quality.apply_to_score(score, quality)
        rankable = (
            scenario.assessment_type == "full"
            and quality.usable
            and score.evidence_status != "unusable"
        )
        checks = self._scenario_checks(
            scenario,
            scorer,
            score,
            quality.status,
            rankable,
        )
        return ScenarioResult(
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.version,
            passed=all(check.passed for check in checks),
            rankable=rankable,
            assessment_type=scenario.assessment_type,
            score=score.total_score,
            confidence=score.confidence.value,
            evidence_status=score.evidence_status,
            evidence_quality_status=quality.status,
            component_scores=score.component_scores,
            checks=checks,
        )

    def _scenario_checks(
        self,
        scenario: BenchmarkScenario,
        scorer: OpportunityScorer,
        score: OpportunityScore,
        quality_status: str,
        rankable: bool,
    ) -> list[CheckResult]:
        expected = scenario.expectations
        checks = [
            _check("rankable", rankable == expected.rankable, expected.rankable, rankable),
            _check(
                "assessment_type",
                scenario.assessment_type == expected.assessment_type,
                expected.assessment_type,
                scenario.assessment_type,
            ),
            _check(
                "confidence",
                score.confidence.value in expected.confidence_in,
                expected.confidence_in,
                score.confidence.value,
            ),
            _check(
                "quality_status",
                quality_status in expected.quality_status_in,
                expected.quality_status_in,
                quality_status,
            ),
            _range_check("score", score.total_score, expected.score_range),
        ]
        if expected.evidence_status_in:
            checks.append(
                _check(
                    "evidence_status",
                    score.evidence_status in expected.evidence_status_in,
                    expected.evidence_status_in,
                    score.evidence_status,
                )
            )
        checks.extend(
            _range_check(component, score.component_scores.get(component, 0), value_range)
            for component, value_range in expected.component_ranges.items()
        )
        checks.extend(
            self._invariant_check(invariant, scenario, scorer, score, quality_status, rankable)
            for invariant in expected.invariants
        )
        return checks

    def _invariant_check(
        self,
        invariant: str,
        scenario: BenchmarkScenario,
        scorer: OpportunityScorer,
        score: OpportunityScore,
        quality_status: str,
        rankable: bool,
    ) -> CheckResult:
        if invariant == "score_between_0_and_100":
            return _check(invariant, 0 <= score.total_score <= 100, "0..100", score.total_score)
        if invariant == "components_within_weights":
            violations = {
                name: value
                for name, value in score.component_scores.items()
                if not 0 <= value <= float(scorer.weights[name])
            }
            return _check(invariant, not violations, {}, violations)
        if invariant == "config_identity_matches":
            actual = [score.scoring_version, score.scoring_config_hash]
            expected = [str(scorer.config["version"]), scorer.config_hash]
            return _check(invariant, actual == expected, expected, actual)
        if invariant == "full_rankable_requires_quality":
            passed = not rankable or (
                scenario.assessment_type == "full" and quality_status != "fail"
            )
            return _check(invariant, passed, True, passed)
        if invariant == "preliminary_not_rankable":
            passed = scenario.assessment_type != "preliminary" or not rankable
            return _check(invariant, passed, True, passed)
        return _check(invariant, False, "supported invariant", invariant)

    def _pairwise_result(
        self,
        expectation: Any,
        by_id: dict[str, ScenarioResult],
    ) -> PairwiseResult:
        preferred = by_id[expectation.preferred]
        other = by_id[expectation.over]
        preferred_value = (
            preferred.component_scores.get(expectation.component, 0)
            if expectation.component
            else preferred.score
        )
        other_value = (
            other.component_scores.get(expectation.component, 0)
            if expectation.component
            else other.score
        )
        return PairwiseResult(
            preferred=expectation.preferred,
            over=expectation.over,
            reason=expectation.reason,
            component=expectation.component,
            minimum_margin=expectation.minimum_margin,
            preferred_value=preferred_value,
            other_value=other_value,
            passed=preferred_value >= other_value + expectation.minimum_margin,
        )

    def _classification_benchmark(self) -> dict[str, Any]:
        cases = []
        confusion: dict[str, dict[str, int]] = {}
        for case in self.classification_library.cases:
            service = self._service(case.service_family_id)
            result = classify_result(
                case.result,
                service=service,
                market=case.market,
                providers=case.providers,
            )
            confidence = float(result.classification_confidence or 0)
            passed = (
                result.classification == case.expected_classification
                and case.confidence_range[0] <= confidence <= case.confidence_range[1]
            )
            confusion.setdefault(case.expected_classification, {})
            confusion[case.expected_classification][result.classification] = (
                confusion[case.expected_classification].get(result.classification, 0) + 1
            )
            cases.append(
                {
                    "case_id": case.case_id,
                    "expected": case.expected_classification,
                    "actual": result.classification,
                    "confidence": confidence,
                    "confidence_range": list(case.confidence_range),
                    "passed": passed,
                }
            )
        passed_count = sum(bool(case["passed"]) for case in cases)
        return {
            "version": self.classification_library.version,
            "total": len(cases),
            "passed": passed_count,
            "failed": len(cases) - passed_count,
            "accuracy": passed_count / max(1, len(cases)),
            "confusion": confusion,
            "cases": cases,
        }

    def _provider_benchmark(self, scorer: OpportunityScorer) -> dict[str, Any]:
        cases = []
        scores: dict[str, float] = {}
        threshold = float(scorer.config["providers"]["suitable_threshold"])
        for case in self.provider_library.cases:
            provider = score_provider_suitability(
                [case.provider],
                self._service(case.service_family_id),
                case.market,
                scorer.config["providers"],
            )[0]
            score = float(provider.suitability_score or 0)
            scores[case.case_id] = score
            suitable = score >= threshold and (
                provider.suitability_signals["status_certainty"]["normalized"] > 0
            )
            passed = (
                case.expected_score_range[0] <= score <= case.expected_score_range[1]
                and suitable == case.expected_suitable
            )
            cases.append(
                {
                    "case_id": case.case_id,
                    "score": score,
                    "expected_score_range": list(case.expected_score_range),
                    "suitable": suitable,
                    "expected_suitable": case.expected_suitable,
                    "passed": passed,
                    "signals": provider.suitability_signals,
                }
            )
        pairwise = [
            {
                "preferred": item.preferred,
                "over": item.over,
                "reason": item.reason,
                "preferred_score": scores[item.preferred],
                "other_score": scores[item.over],
                "passed": scores[item.preferred] > scores[item.over],
            }
            for item in self.provider_library.pairwise_expectations
        ]
        passed_count = sum(bool(case["passed"]) for case in cases)
        pairwise_passed = sum(bool(item["passed"]) for item in pairwise)
        return {
            "version": self.provider_library.version,
            "total": len(cases) + len(pairwise),
            "passed": passed_count + pairwise_passed,
            "failed": (len(cases) - passed_count) + (len(pairwise) - pairwise_passed),
            "accuracy": (passed_count + pairwise_passed)
            / max(1, len(cases) + len(pairwise)),
            "suitable_threshold": threshold,
            "cases": cases,
            "pairwise": pairwise,
        }

    def _service(self, service_family_id: str) -> Any:
        resolution = self.service_catalog.resolve(service_family_id)
        if resolution is None:
            raise CalibrationConfigError(
                f"Unknown benchmark service family {service_family_id}"
            )
        return resolution.service

    def _scorer(self, scoring_version: str) -> OpportunityScorer:
        configured_path = self.manifest.scoring_configs.get(scoring_version)
        if configured_path is None:
            raise CalibrationConfigError(
                f"Unknown scoring version {scoring_version}; configured versions: "
                f"{', '.join(sorted(self.manifest.scoring_configs))}"
            )
        scorer = OpportunityScorer(project_path(self.project_root, configured_path))
        if str(scorer.config["version"]) != scoring_version:
            raise CalibrationConfigError(
                f"Scoring config {configured_path} declares {scorer.config['version']}, "
                f"not {scoring_version}"
            )
        return scorer

    def _scoring_config_checks(
        self,
        expected_version: str,
        path: Path,
        raw: dict[str, Any],
    ) -> list[CheckResult]:
        weights = raw.get("weights", {})
        demand = raw.get("demand", {})
        commercial = raw.get("commercial", {})
        return [
            _check(
                f"{expected_version}:declared_version",
                str(raw.get("version")) == expected_version,
                expected_version,
                raw.get("version"),
            ),
            _check(
                f"{expected_version}:filename",
                path.stem == expected_version,
                expected_version,
                path.stem,
            ),
            _check(
                f"{expected_version}:weights_total",
                abs(sum(float(value) for value in weights.values()) - 100) < 0.0001,
                100,
                sum(float(value) for value in weights.values()),
            ),
            _check(
                f"{expected_version}:demand_shares",
                abs(
                    float(demand.get("service_attractiveness_share", 0))
                    + float(demand.get("market_attractiveness_share", 0))
                    - 1
                )
                < 0.0001,
                1,
                float(demand.get("service_attractiveness_share", 0))
                + float(demand.get("market_attractiveness_share", 0)),
            ),
            _check(
                f"{expected_version}:commercial_signal_shares",
                abs(
                    sum(
                        float(value)
                        for value in commercial.get("signal_shares", {}).values()
                    )
                    - 1
                )
                < 0.0001,
                1,
                sum(
                    float(value)
                    for value in commercial.get("signal_shares", {}).values()
                ),
            ),
        ]


def _fresh_evidence(
    scenario: BenchmarkScenario,
    now: datetime,
) -> dict[str, Any]:
    freshness = scenario.evidence.freshness
    metrics = [
        metric.model_copy(
            update={"source_timestamp": now - timedelta(days=freshness.keyword_age_days)}
        )
        for metric in scenario.evidence.keyword_metrics
    ]
    snapshots = [
        snapshot.model_copy(
            update={"captured_at": now - timedelta(days=freshness.serp_age_days)}
        )
        for snapshot in scenario.evidence.serp_snapshots
    ]
    competitors = [
        competitor.model_copy(
            update={"captured_at": now - timedelta(days=freshness.competitor_age_days)}
        )
        for competitor in scenario.evidence.competitors
    ]
    providers = [
        provider.model_copy(
            update={"source_timestamp": now - timedelta(days=freshness.provider_age_days)}
        )
        for provider in scenario.evidence.providers
    ]
    return {
        "keyword_metrics": metrics,
        "serp_snapshots": snapshots,
        "competitors": competitors,
        "providers": providers,
    }


def _classify_snapshots(
    snapshots: list[SerpSnapshot],
    *,
    service: Any,
    market: Any,
    providers: list[Any],
) -> list[SerpSnapshot]:
    return [
        snapshot.model_copy(
            update={
                "results": [
                    classify_result(
                        result,
                        service=service,
                        market=market,
                        providers=providers,
                    )
                    for result in snapshot.results
                ]
            }
        )
        for snapshot in snapshots
    ]


def _check(
    name: str,
    passed: bool,
    expected: Any,
    actual: Any,
    message: str = "",
) -> CheckResult:
    return CheckResult(
        check=name,
        passed=passed,
        expected=expected,
        actual=actual,
        message=message,
    )


def _range_check(
    name: str,
    value: float,
    expected_range: tuple[float, float],
) -> CheckResult:
    return _check(
        name,
        expected_range[0] <= value <= expected_range[1],
        list(expected_range),
        value,
    )


def _summary(values: list[bool]) -> BenchmarkSummary:
    passed = sum(values)
    return BenchmarkSummary(total=len(values), passed=passed, failed=len(values) - passed)


def _component_distributions(
    results: list[ScenarioResult],
) -> dict[str, dict[str, float]]:
    names = sorted(
        {component for result in results for component in result.component_scores}
    )
    distributions = {}
    for name in names:
        values = [result.component_scores[name] for result in results]
        distributions[name] = {
            "minimum": round(min(values), 2),
            "maximum": round(max(values), 2),
            "mean": round(mean(values), 2),
            "median": round(median(values), 2),
        }
    return distributions


def _evidence_gate_confusion(results: list[ScenarioResult]) -> dict[str, int]:
    confusion = {
        "expected_rankable_predicted_rankable": 0,
        "expected_rankable_predicted_unrankable": 0,
        "expected_unrankable_predicted_rankable": 0,
        "expected_unrankable_predicted_unrankable": 0,
    }
    for result in results:
        expected_check = next(check for check in result.checks if check.check == "rankable")
        expected = bool(expected_check.expected)
        key = (
            f"expected_{'rankable' if expected else 'unrankable'}_"
            f"predicted_{'rankable' if result.rankable else 'unrankable'}"
        )
        confusion[key] += 1
    return confusion
