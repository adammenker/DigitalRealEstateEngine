from rank_rent.domain.models import (
    CompetitorMetric,
    KeywordMetric,
    Market,
    ProviderCandidate,
    SerpResult,
    SerpSnapshot,
    ServiceFamily,
)
from rank_rent.scoring.score import OpportunityScorer
from rank_rent.services.evidence_quality import EvidenceQualityEvaluator


def _service() -> ServiceFamily:
    return ServiceFamily(
        id="water_heater_services",
        display_name="Water Heater Services",
        seed_queries=["water heater repair", "water heater replacement"],
        provider_categories=["plumber", "water heater installation service"],
    )


def _provider(name: str, service_fit: float, geography_fit: float) -> ProviderCandidate:
    return ProviderCandidate(
        name=name,
        suitability_signals={
            "service_fit": {"normalized": service_fit},
            "geographic_fit": {"normalized": geography_fit},
        },
    )


def test_unrelated_sandbox_evidence_is_unusable_for_ranking() -> None:
    evaluator = EvidenceQualityEvaluator()
    metrics = [
        KeywordMetric(
            keyword="phone",
            canonical_keyword="phone",
            intent="commercial",
            search_volume=100,
        ),
        KeywordMetric(
            keyword="watch",
            canonical_keyword="watch",
            intent="commercial",
            search_volume=100,
        ),
    ]
    serps = [
        SerpSnapshot(
            query="phone",
            market_id="st-louis",
            results=[
                SerpResult(
                    order=1,
                    url="https://example.com",
                    domain="example.com",
                    title="Example",
                    classification="unknown",
                )
            ],
        )
    ]

    assessment = evaluator.assess(
        service=_service(),
        metrics=metrics,
        serp_snapshots=serps,
        competitors=[],
        providers=[_provider("Pizza Shop", 0, 0)],
        assessment_type="preliminary",
    )

    assert assessment.status == "fail"
    assert {
        issue.code for issue in assessment.issues if issue.severity == "error"
    } == {
        "keyword_service_relevance",
        "representative_query_relevance",
        "provider_service_relevance",
    }


def test_relevant_full_evidence_passes_quality_gate() -> None:
    evaluator = EvidenceQualityEvaluator()
    metrics = [
        KeywordMetric(
            keyword="water heater repair",
            canonical_keyword="water heater repair",
            intent="transactional",
            search_volume=100,
        )
    ]
    serps = [
        SerpSnapshot(
            query="water heater repair",
            market_id="st-louis",
            results=[
                SerpResult(
                    order=1,
                    url="https://local-plumber.example/water-heaters",
                    domain="local-plumber.example",
                    title="Water Heater Repair",
                    classification="local_provider",
                )
            ],
        )
    ]
    competitors = [
        CompetitorMetric(url=f"https://{index}.example", domain=f"{index}.example")
        for index in range(3)
    ]

    assessment = evaluator.assess(
        service=_service(),
        metrics=metrics,
        serp_snapshots=serps,
        competitors=competitors,
        providers=[_provider("St. Louis Plumbing", 0.95, 1)],
        assessment_type="full",
    )

    assert assessment.status == "pass"
    assert assessment.issues == []


def test_failed_quality_gate_caps_score_and_confidence() -> None:
    evaluator = EvidenceQualityEvaluator()
    score = OpportunityScorer().score(
        metrics=[],
        serp_snapshots=[],
        competitors=[],
        providers=[],
        market=Market(id="test", display_name="Test"),
        assessment_type="preliminary",
    ).model_copy(update={"total_score": 80, "uncapped_total_score": 80})
    assessment = evaluator.assess(
        service=_service(),
        metrics=[],
        serp_snapshots=[],
        competitors=[],
        providers=[],
        assessment_type="preliminary",
    )

    result = evaluator.apply_to_score(score, assessment)

    assert result.total_score == 35
    assert result.evidence_status == "unusable"
    assert result.confidence.value == "insufficient"
    assert result.input_measurements["evidence_quality"]["status"] == "fail"
