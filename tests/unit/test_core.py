import asyncio

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


def test_serp_classification_directory_and_local_provider() -> None:
    directory = classify_result(
        SerpResult(order=1, url="https://www.yelp.com/search", domain="www.yelp.com", title="Yelp")
    )
    local = classify_result(
        SerpResult(
            order=2,
            url="https://stamfordtrustedpros.example/repair",
            domain="stamfordtrustedpros.example",
            title="Water Heater Repair Pros",
        )
    )
    assert directory.classification == "directory"
    assert directory.is_directory is True
    assert local.classification == "local_provider"
    assert local.is_local_provider is True


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

    assert strong.component_scores["organic_accessibility"] <= weak.component_scores["organic_accessibility"]


def test_local_pack_and_ads_do_not_improve_serp_accessibility() -> None:
    scorer = OpportunityScorer()
    base = scorer.score([_metric()], [_snapshot()], [], [_provider()])
    displaced = scorer.score(
        [_metric()],
        [_snapshot(["local_pack", "ads_top"])],
        [],
        [_provider()],
    )

    assert displaced.component_scores["serp_accessibility"] <= base.component_scores["serp_accessibility"]


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
    assert score.input_measurements["raw_national_service_demand"] == 100
    assert score.input_measurements["estimated_market_demand"] is None
    assert "country granularity" in score.assumptions[0]
