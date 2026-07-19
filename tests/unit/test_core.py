import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from rank_rent.domain.models import (
    CompetitorMetric,
    KeywordCandidate,
    KeywordMetric,
    Market,
    ProviderCandidate,
    SerpResult,
    SerpSnapshot,
    ServiceFamily,
)
from rank_rent.integrations.domain_availability.mock import MockDomainAvailabilityProvider
from rank_rent.scoring.score import OpportunityScorer
from rank_rent.scoring.serp import classify_result
from rank_rent.services.competitors import enrich_competitors
from rank_rent.services.domains import generate_domain_candidates
from rank_rent.services.keywords import (
    dedupe_and_filter_keywords,
    rank_and_cluster_keyword_metrics,
)


def test_keyword_dedupe_and_negative_filter() -> None:
    result = dedupe_and_filter_keywords(
        [
            KeywordCandidate(keyword="Water Heater Repair"),
            KeywordCandidate(keyword="water heater repair"),
            KeywordCandidate(keyword="water heater repair jobs"),
        ],
        ["jobs"],
    )
    assert len(result) == 3
    assert result[0].included is True
    assert result[1].included is False
    assert result[1].excluded_reason == "duplicate_exact"
    assert result[2].included is False
    assert result[2].excluded_reason == "negative_term:jobs"


def test_keyword_close_variants_are_grouped_and_not_double_counted() -> None:
    service = ServiceFamily(id="water_heater", display_name="Water Heater Repair")
    market = Market(id="stamford_ct", display_name="Stamford, CT", state="CT", cities=["Stamford"])
    metrics = [
        KeywordMetric(
            keyword="water heater repair",
            canonical_keyword="water heater repair",
            intent="commercial",
            search_volume=100,
            cpc=10,
        ),
        KeywordMetric(
            keyword="water heater repairs near me",
            canonical_keyword="water heater repairs near me",
            intent="transactional",
            search_volume=90,
            cpc=18,
        ),
    ]

    plan = rank_and_cluster_keyword_metrics(
        metrics,
        service=service,
        market=market,
        selected_limit=1,
    )
    score = OpportunityScorer().score(plan.metrics, [_snapshot()], [], [_provider()])

    assert len(plan.clusters) == 1
    assert plan.clusters[0].combined_volume == 100
    assert any(metric.included is False for metric in plan.metrics)
    assert score.input_measurements["deduplicated_search_volume"] == 100
    assert score.input_measurements["excluded_keyword_metric_count"] == 1


def test_serp_representatives_are_selected_by_metric_value_not_input_order() -> None:
    service = ServiceFamily(id="drywall", display_name="Drywall Repair")
    market = Market(id="st_louis_mo", display_name="St. Louis, MO", state="MO", cities=["St. Louis"])
    metrics = [
        KeywordMetric(
            keyword="drywall",
            canonical_keyword="drywall",
            intent="informational",
            search_volume=900,
            cpc=1,
        ),
        KeywordMetric(
            keyword="emergency drywall repair st louis",
            canonical_keyword="emergency drywall repair st louis",
            intent="transactional",
            search_volume=120,
            cpc=24,
        ),
    ]

    plan = rank_and_cluster_keyword_metrics(
        metrics,
        service=service,
        market=market,
        selected_limit=1,
    )

    assert plan.selected_serp_keywords == ["emergency drywall repair st louis"]
    assert plan.decisions[-2].representative is True


def test_serp_classification_known_domains_and_ambiguous_results() -> None:
    directory = classify_result(
        SerpResult(order=1, url="https://www.yelp.com/search", domain="www.yelp.com", title="Yelp")
    )
    marketplace = classify_result(
        SerpResult(
            order=2,
            url="https://www.thumbtack.com/search",
            domain="www.thumbtack.com",
            title="Thumbtack",
        )
    )
    ambiguous_org = classify_result(
        SerpResult(
            order=3,
            url="https://example.org/water-heater-repair",
            domain="example.org",
            title="Water Heater Repair",
        )
    )
    ambiguous_local = classify_result(
        SerpResult(
            order=4,
            url="https://stamfordtrustedpros.example/repair",
            domain="stamfordtrustedpros.example",
            title="Water Heater Repair Pros",
        )
    )
    assert directory.classification == "directory"
    assert directory.is_directory is True
    assert marketplace.classification == "marketplace"
    assert ambiguous_org.classification == "unknown"
    assert ambiguous_local.classification == "unknown"


def test_serp_local_provider_requires_service_market_and_business_evidence() -> None:
    service = ServiceFamily(id="water-heater-repair", display_name="Water Heater Repair")
    market = Market(id="stamford-ct", display_name="Stamford, CT", state="CT", cities=["Stamford"])
    local = classify_result(
        SerpResult(
            order=1,
            url="https://stamford-water-heater-pros.example/repair",
            domain="stamford-water-heater-pros.example",
            title="Licensed Water Heater Repair in Stamford CT",
            description="Family owned. Call now to schedule service.",
        ),
        service=service,
        market=market,
    )
    provider_match = classify_result(
        SerpResult(
            order=2,
            url="https://localwaterpros.example/water-heater",
            domain="localwaterpros.example",
            title="Water Heater Repair Stamford",
        ),
        service=service,
        market=market,
        providers=[
            ProviderCandidate(
                name="Local Water Pros",
                website="https://localwaterpros.example",
                business_status="open",
            )
        ],
    )

    assert local.classification == "local_provider"
    assert local.is_local_provider is True
    assert local.classification_evidence["business_identity_terms"]
    assert provider_match.classification == "local_provider"
    assert provider_match.classification_evidence["provider_match"]["type"] == "website_domain"


def test_domain_candidates_are_ranked_without_risky_claims() -> None:
    service = ServiceFamily(
        id="water_heater_services",
        display_name="Water Heater Services",
        seed_queries=["water heater repair"],
    )
    market = Market(
        id="stamford_ct",
        display_name="Stamford, CT",
        state="CT",
        cities=["Stamford"],
    )
    domains = asyncio.run(generate_domain_candidates(service, market, MockDomainAvailabilityProvider()))
    assert len(domains) >= 5
    assert domains[0].rank == 1
    assert all("best" not in domain.domain for domain in domains)


def test_service_and_market_default_slugs_are_populated() -> None:
    service = ServiceFamily(id="drywall repair", display_name="Drywall Repair")
    market = Market(id="St. Louis, MO", display_name="St. Louis, MO")

    assert service.slug == "drywall-repair"
    assert market.slug == "st-louis-mo"


def _metric() -> KeywordMetric:
    return KeywordMetric(
        keyword="drywall repair",
        canonical_keyword="drywall repair",
        intent="commercial",
        search_volume=100,
        cpc=12,
    )


def _snapshot(features: list[str] | None = None) -> SerpSnapshot:
    return SerpSnapshot(
        query="drywall repair",
        market_id="st-louis-mo",
        features_present=features or [],
        results=[
            SerpResult(
                order=1,
                result_type="organic",
                url="https://local.example/drywall-repair",
                domain="local.example",
                title="Drywall Repair Contractor",
            )
        ],
    )


def _provider() -> ProviderCandidate:
    return ProviderCandidate(
        name="Local Drywall Co",
        website="https://local.example",
        business_status="open",
    )


def _complete_serp_sample(*, captured_at: datetime | None = None) -> list[SerpSnapshot]:
    return [
        _snapshot().model_copy(
            update={
                "query": f"drywall repair {index}",
                "captured_at": captured_at or datetime.now(UTC),
            }
        )
        for index in range(3)
    ]


def _complete_competitor_sample() -> list[CompetitorMetric]:
    return [
        CompetitorMetric(
            url=f"https://competitor-{index}.example",
            domain=f"competitor-{index}.example",
            referring_domains=50,
            page_relevance_score=0.7,
            local_relevance=0.7,
        )
        for index in range(3)
    ]


def _complete_provider_sample() -> list[ProviderCandidate]:
    return [
        _provider(),
        ProviderCandidate(
            name="Second Local Drywall Co",
            website="https://second-local.example",
            phone="555-0100",
            business_status="open",
        ),
    ]


def test_stronger_local_competitors_do_not_improve_organic_score() -> None:
    scorer = OpportunityScorer()
    weak = scorer.score(
        [_metric()],
        [_snapshot()],
        [CompetitorMetric(url="https://weak.example", domain="weak.example", referring_domains=10, local_relevance=0.1)],
        [_provider()],
    )
    strong = scorer.score(
        [_metric()],
        [_snapshot()],
        [
            CompetitorMetric(
                url="https://strong.example",
                domain="strong.example",
                referring_domains=500,
                local_relevance=0.9,
            )
        ],
        [_provider()],
    )

    assert strong.component_scores["competitor_weakness"] <= weak.component_scores["competitor_weakness"]


def test_more_relevant_competitor_reduces_competitor_weakness_with_same_backlinks() -> None:
    scorer = OpportunityScorer()
    low_relevance = scorer.score(
        [_metric()],
        [_snapshot()],
        [
            CompetitorMetric(
                url="https://weak-fit.example",
                domain="weak-fit.example",
                referring_domains=75,
                page_relevance_score=0.35,
                local_relevance=0.35,
            )
        ],
        [_provider()],
    )
    high_relevance = scorer.score(
        [_metric()],
        [_snapshot()],
        [
            CompetitorMetric(
                url="https://direct-threat.example",
                domain="direct-threat.example",
                referring_domains=75,
                page_relevance_score=0.9,
                local_relevance=0.9,
            )
        ],
        [_provider()],
    )

    assert (
        high_relevance.component_scores["competitor_weakness"]
        < low_relevance.component_scores["competitor_weakness"]
    )


def test_competitor_enrichment_preserves_distinct_competitor_archetypes() -> None:
    service = ServiceFamily(id="drywall", display_name="Drywall Repair")
    market = Market(
        id="st-louis-mo",
        display_name="St. Louis, MO",
        state="MO",
        cities=["St. Louis"],
    )
    archetypes = {
        "directory.example": "directory",
        "marketplace.example": "marketplace",
        "lead-generator.example": "lead_generator",
        "national-brand.example": "national_brand",
    }
    snapshot = SerpSnapshot(
        query="drywall repair st louis",
        market_id=market.id,
        results=[
            SerpResult(
                order=order,
                url=f"https://{domain}/drywall-repair-st-louis",
                domain=domain,
                title="Drywall Repair in St. Louis",
                classification=archetype,
            )
            for order, (domain, archetype) in enumerate(archetypes.items(), start=1)
        ],
    )
    enriched = enrich_competitors(
        [
            CompetitorMetric(
                url=f"https://{domain}/drywall-repair-st-louis",
                domain=domain,
            )
            for domain in archetypes
        ],
        [snapshot],
        service,
        market,
    )

    by_archetype = {
        competitor.relevance_signals["competitor_archetype"]: competitor
        for competitor in enriched
    }
    expected_flags = {
        "directory": "is_directory_aggregator",
        "marketplace": "is_marketplace",
        "lead_generator": "is_lead_generator",
        "national_brand": "is_national_service_brand",
    }
    for archetype, own_flag in expected_flags.items():
        signals = by_archetype[archetype].relevance_signals
        assert signals[own_flag] is True
        assert all(
            signals[flag] is (flag == own_flag)
            for flag in expected_flags.values()
        )
        assert "is_aggregator" not in signals


def test_competitor_archetypes_receive_distinct_weakness_adjustments() -> None:
    scorer = OpportunityScorer()

    def weakness(archetype: str) -> float:
        score = scorer.score(
            [_metric()],
            [_snapshot()],
            [
                CompetitorMetric(
                    url=f"https://{archetype}.example",
                    domain=f"{archetype}.example",
                    referring_domains=75,
                    page_relevance_score=0.6,
                    local_relevance=0.6,
                    page_type=archetype,
                    relevance_signals={"competitor_archetype": archetype},
                )
            ],
            [_provider()],
        )
        return score.component_scores["competitor_weakness"]

    scores = {
        archetype: weakness(archetype)
        for archetype in (
            "directory",
            "marketplace",
            "lead_generator",
            "local_provider",
            "national_brand",
        )
    }

    assert (
        scores["directory"]
        > scores["marketplace"]
        > scores["lead_generator"]
        > scores["local_provider"]
        > scores["national_brand"]
    )


def test_component_traces_are_fact_specific_and_reconcile_to_component_scores() -> None:
    score = OpportunityScorer().score(
        [_metric()],
        _complete_serp_sample(),
        _complete_competitor_sample(),
        _complete_provider_sample(),
        Market(id="st-louis-mo", display_name="St. Louis, MO"),
        source_mode="live",
    )

    assert set(score.component_details) == set(score.component_scores)
    for component, detail in score.component_details.items():
        assert detail.score == score.component_scores[component]
        assert detail.maximum_score > 0
        assert detail.formula
        assert detail.explanation
        assert detail.calculation_steps
        assert sum(step.points for step in detail.calculation_steps) == pytest.approx(
            detail.score,
            abs=0.02,
        )

    competitor = score.component_details["competitor_weakness"]
    commercial = score.component_details["commercial_value"]
    assert "Median referring domains" in competitor.calculation_steps[0].detail
    assert "per_competitor" in competitor.inputs
    assert "average_cpc" not in competitor.inputs
    assert "average_cpc" in commercial.inputs
    assert competitor.formula.startswith("mean(clamp(")


def test_local_pack_and_ads_do_not_improve_click_availability() -> None:
    scorer = OpportunityScorer()
    base = scorer.score([_metric()], [_snapshot()], [], [_provider()])
    displaced = scorer.score(
        [_metric()],
        [_snapshot(["local_pack", "ads_top"])],
        [],
        [_provider()],
    )

    assert displaced.component_scores["organic_click_availability"] <= base.component_scores["organic_click_availability"]


def test_marketplaces_and_publishers_reduce_click_availability() -> None:
    scorer = OpportunityScorer()
    base_result = _snapshot().results[0]
    base = scorer.score([_metric()], [_snapshot()], [], [_provider()])
    marketplace = scorer.score(
        [_metric()],
        [
            _snapshot().model_copy(
                update={
                    "results": [
                        base_result.model_copy(update={"classification": "marketplace"})
                    ]
                }
            )
        ],
        [],
        [_provider()],
    )
    publisher = scorer.score(
        [_metric()],
        [
            _snapshot().model_copy(
                update={
                    "results": [
                        base_result.model_copy(
                            update={"classification": "informational_publisher"}
                        )
                    ]
                }
            )
        ],
        [],
        [_provider()],
    )

    assert (
        marketplace.component_scores["organic_click_availability"]
        < publisher.component_scores["organic_click_availability"]
        < base.component_scores["organic_click_availability"]
    )


def test_shopping_features_reduce_click_availability_even_without_a_result_url() -> None:
    scorer = OpportunityScorer()
    base = scorer.score([_metric()], [_snapshot()], [], [_provider()])
    shopping = scorer.score(
        [_metric()],
        [_snapshot(["popular_products"])],
        [],
        [_provider()],
    )

    assert (
        shopping.component_scores["organic_click_availability"]
        < base.component_scores["organic_click_availability"]
    )
    assert shopping.input_measurements["serp_result_type_composition"] == {"organic": 1}


def test_missing_competitor_metrics_prevents_high_confidence() -> None:
    score = OpportunityScorer().score([_metric()], [_snapshot()], [], [_provider()])

    assert score.confidence.value != "high"
    assert "competitor_metrics" in score.missing_fields


def test_country_level_keyword_volume_is_labeled_as_national_demand() -> None:
    metric = _metric()
    metric.market_granularity = "country"
    metric.source = "dataforseo:historical_search_volume"

    score = OpportunityScorer().score([metric], [_snapshot()], [], [_provider()])

    assert score.input_measurements["keyword_metric_granularities"] == ["country"]
    assert score.input_measurements["national_service_demand"] == 100
    assert score.input_measurements["estimated_market_demand"] is None
    assert "National keyword volume supports service attractiveness only" in score.assumptions[0]
    assert "local_demand" in score.missing_fields
    assert score.input_measurements["demand_score_inputs"]["market_score"] == 0


def test_national_only_demand_cannot_match_measured_local_demand_or_high_confidence() -> None:
    scorer = OpportunityScorer()
    market = Market(id="st-louis", display_name="St. Louis, MO")
    national_metric = _metric().model_copy(
        update={
            "search_volume": 900,
            "market_granularity": "country",
            "source": "dataforseo:historical_search_volume",
        }
    )
    local_metric = national_metric.model_copy(update={"market_granularity": "city"})
    evidence = (
        _complete_serp_sample(),
        _complete_competitor_sample(),
        _complete_provider_sample(),
    )

    national = scorer.score(
        [national_metric],
        *evidence,
        market,
        source_mode="live",
    )
    local = scorer.score(
        [local_metric],
        *evidence,
        market,
        source_mode="live",
    )

    assert (
        national.component_scores["demand_evidence"]
        < local.component_scores["demand_evidence"]
    )
    assert national.confidence.value == "low"
    assert local.confidence.value == "high"
    assert national.input_measurements["confidence_model"]["market_demand_kind"] == "missing"


def test_population_estimates_differentiate_markets_with_same_national_demand() -> None:
    scorer = OpportunityScorer()
    metric = _metric().model_copy(
        update={
            "search_volume": 900,
            "market_granularity": "country",
            "source": "dataforseo:historical_search_volume",
        }
    )
    small_market = Market(
        id="small",
        display_name="Small Market",
        resolution_metadata={"population": 100_000, "country_population": 100_000_000},
    )
    large_market = Market(
        id="large",
        display_name="Large Market",
        resolution_metadata={"population": 10_000_000, "country_population": 100_000_000},
    )
    evidence = (
        _complete_serp_sample(),
        _complete_competitor_sample(),
        _complete_provider_sample(),
    )

    small = scorer.score([metric], *evidence, small_market, source_mode="live")
    large = scorer.score([metric], *evidence, large_market, source_mode="live")

    assert small.input_measurements["national_service_demand"] == 900
    assert small.input_measurements["estimated_market_demand"] == 0.9
    assert large.input_measurements["estimated_market_demand"] == 90
    assert (
        large.component_scores["demand_evidence"]
        > small.component_scores["demand_evidence"]
    )
    assert small.confidence.value == "medium"
    assert large.confidence.value == "medium"


def test_confidence_accounts_for_source_age_and_sample_size() -> None:
    scorer = OpportunityScorer()
    metric = _metric().model_copy(
        update={
            "market_granularity": "city",
            "source": "dataforseo:historical_search_volume",
        }
    )
    competitors = _complete_competitor_sample()
    providers = _complete_provider_sample()
    live = scorer.score(
        [metric],
        _complete_serp_sample(),
        competitors,
        providers,
        source_mode="live",
    )
    sandbox = scorer.score(
        [metric],
        _complete_serp_sample(),
        competitors,
        providers,
        source_mode="sandbox",
    )
    stale = scorer.score(
        [metric],
        _complete_serp_sample(captured_at=datetime.now(UTC) - timedelta(days=31)),
        competitors,
        providers,
        source_mode="live",
    )
    undersampled = scorer.score(
        [metric],
        [_snapshot()],
        competitors,
        providers,
        source_mode="live",
    )

    assert live.confidence.value == "high"
    assert sandbox.confidence.value == "medium"
    assert stale.confidence.value == "medium"
    assert undersampled.confidence.value == "medium"
    assert {
        item["factor"]
        for item in undersampled.input_measurements["confidence_model"]["deductions"]
    } >= {"limited_serp_sample"}
