import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from rank_rent.domain.models import (
    CompetitorMetric,
    CompetitorSerpObservation,
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
from rank_rent.services.competitors import enrich_competitors, select_competitor_urls
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
                classification="local_provider",
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


def _rankable_evidence() -> tuple[
    KeywordMetric,
    list[SerpSnapshot],
    list[CompetitorMetric],
    list[ProviderCandidate],
]:
    providers = [
        provider.model_copy(update={"suitability_score": 80})
        for provider in _complete_provider_sample()
    ]
    return (
        _metric().model_copy(update={"source": "dataforseo:historical_search_volume"}),
        _complete_serp_sample(),
        _complete_competitor_sample(),
        providers,
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


def test_service_and_local_relevance_are_distinct_competitor_threat_signals() -> None:
    scorer = OpportunityScorer()

    def score_relevance(
        service_relevance: float,
        local_relevance: float,
    ):
        return scorer.score(
            [_metric()],
            [_snapshot()],
            [
                CompetitorMetric(
                    url="https://competitor.example",
                    domain="competitor.example",
                    referring_domains=75,
                    page_relevance_score=service_relevance,
                    local_relevance=local_relevance,
                )
            ],
            [_provider()],
        )

    service_only = score_relevance(1, 0)
    local_only = score_relevance(0, 1)
    service_and_local = score_relevance(1, 1)

    assert (
        service_and_local.component_scores["competitor_weakness"]
        < service_only.component_scores["competitor_weakness"]
        < local_only.component_scores["competitor_weakness"]
    )
    trace = service_and_local.component_details["competitor_weakness"]
    competitor = trace.inputs["per_competitor"][0]
    assert competitor["service_relevance"] == 1
    assert competitor["local_relevance"] == 1
    assert competitor["relevance_interaction"] == 1
    assert competitor["direct_relevance"] == 1
    assert trace.inputs["normalized_relevance_signal_weights"] == {
        "service": 0.55,
        "local": 0.25,
        "interaction": 0.2,
    }


def test_higher_ranked_strong_competitor_reduces_weakness_more() -> None:
    scorer = OpportunityScorer()

    def competitor(domain: str, referring_domains: int, position: int) -> CompetitorMetric:
        return CompetitorMetric(
            url=f"https://{domain}",
            domain=domain,
            referring_domains=referring_domains,
            page_relevance_score=0.8,
            local_relevance=0.8,
            representative_query="drywall repair st louis",
            serp_position=position,
        )

    strong_first = scorer.score(
        [_metric()],
        [_snapshot()],
        [
            competitor("strong.example", 500, 1),
            competitor("weak.example", 10, 10),
        ],
        [_provider()],
    )
    weak_first = scorer.score(
        [_metric()],
        [_snapshot()],
        [
            competitor("strong.example", 500, 10),
            competitor("weak.example", 10, 1),
        ],
        [_provider()],
    )

    assert (
        strong_first.component_scores["competitor_weakness"]
        < weak_first.component_scores["competitor_weakness"]
    )


def test_competitor_repeated_across_queries_has_more_influence() -> None:
    metrics = [
        _metric().model_copy(
            update={
                "keyword": query,
                "canonical_keyword": query,
                "search_volume": 100,
                "cpc": 10,
            }
        )
        for query in ("query one", "query two", "query three")
    ]

    def competitor(
        domain: str,
        referring_domains: int,
        queries: tuple[str, ...],
    ) -> CompetitorMetric:
        return CompetitorMetric(
            url=f"https://{domain}",
            domain=domain,
            referring_domains=referring_domains,
            page_relevance_score=0.8,
            local_relevance=0.8,
            representative_query=queries[0],
            serp_position=1,
            serp_observations=[
                CompetitorSerpObservation(
                    query=query,
                    position=1,
                    url=f"https://{domain}/{index}",
                )
                for index, query in enumerate(queries, start=1)
            ],
        )

    strong_once = competitor("strong.example", 500, ("query one",))
    strong_everywhere = competitor(
        "strong.example",
        500,
        ("query one", "query two", "query three"),
    )
    weak_once = competitor("weak.example", 10, ("query one",))
    scorer = OpportunityScorer()

    single_query = scorer.score(
        metrics,
        [_snapshot()],
        [strong_once, weak_once],
        [_provider()],
    )
    repeated = scorer.score(
        metrics,
        [_snapshot()],
        [strong_everywhere, weak_once],
        [_provider()],
    )

    assert (
        repeated.component_scores["competitor_weakness"]
        < single_query.component_scores["competitor_weakness"]
    )
    detail = repeated.component_details["competitor_weakness"]
    assert detail.inputs["competitor_count"] == 2
    assert detail.inputs["observation_count"] == 4
    strong_trace = next(
        item
        for item in detail.inputs["per_competitor"]
        if item["domain"] == "strong.example"
    )
    assert strong_trace["observation_count"] == 3
    assert strong_trace["total_exposure_weight"] == 3


def test_high_value_query_weights_competitor_exposure_more_heavily() -> None:
    high_value = _metric().model_copy(
        update={
            "keyword": "high value query",
            "canonical_keyword": "high value query",
            "search_volume": 1_000,
            "cpc": 20,
        }
    )
    low_value = high_value.model_copy(
        update={
            "keyword": "low value query",
            "canonical_keyword": "low value query",
            "search_volume": 100,
            "cpc": 2,
        }
    )

    def competitor(
        domain: str,
        referring_domains: int,
        query: str,
    ) -> CompetitorMetric:
        return CompetitorMetric(
            url=f"https://{domain}",
            domain=domain,
            referring_domains=referring_domains,
            page_relevance_score=0.8,
            local_relevance=0.8,
            representative_query=query,
            serp_position=1,
            serp_observations=[
                CompetitorSerpObservation(
                    query=query,
                    position=1,
                    url=f"https://{domain}",
                )
            ],
        )

    scorer = OpportunityScorer()
    strong_on_high_value = scorer.score(
        [high_value, low_value],
        [_snapshot()],
        [
            competitor("strong.example", 500, high_value.keyword),
            competitor("weak.example", 10, low_value.keyword),
        ],
        [_provider()],
    )
    strong_on_low_value = scorer.score(
        [high_value, low_value],
        [_snapshot()],
        [
            competitor("strong.example", 500, low_value.keyword),
            competitor("weak.example", 10, high_value.keyword),
        ],
        [_provider()],
    )

    assert (
        strong_on_high_value.component_scores["competitor_weakness"]
        < strong_on_low_value.component_scores["competitor_weakness"]
    )


def test_duplicate_competitor_domain_is_scored_once() -> None:
    scorer = OpportunityScorer()
    duplicate = CompetitorMetric(
        url="https://www.same.example/other-page",
        domain="www.same.example",
        referring_domains=10,
        page_relevance_score=0.2,
        serp_position=4,
    )
    representative = duplicate.model_copy(
        update={
            "url": "https://same.example/best-page",
            "domain": "same.example",
            "serp_position": 1,
        }
    )
    unique_score = scorer.score(
        [_metric()],
        [_snapshot()],
        [representative],
        [_provider()],
    )
    duplicate_score = scorer.score(
        [_metric()],
        [_snapshot()],
        [duplicate, representative],
        [_provider()],
    )

    assert (
        duplicate_score.component_scores["competitor_weakness"]
        == unique_score.component_scores["competitor_weakness"]
    )
    detail = duplicate_score.component_details["competitor_weakness"]
    assert detail.inputs["competitor_count"] == 1
    assert detail.inputs["duplicate_competitor_count"] == 1
    assert detail.inputs["per_competitor"][0]["serp_position"] == 1


def test_competitor_selection_and_enrichment_preserve_serp_provenance() -> None:
    service = ServiceFamily(id="drywall", display_name="Drywall Repair")
    market = Market(
        id="st-louis-mo",
        display_name="St. Louis, MO",
        state="MO",
        cities=["St. Louis"],
    )
    snapshots = [
        SerpSnapshot(
            query="drywall repair st louis",
            market_id=market.id,
            results=[
                SerpResult(
                    order=4,
                    url="https://same.example/service",
                    domain="same.example",
                    title="Drywall Repair in St. Louis",
                ),
                SerpResult(
                    order=2,
                    url="https://other.example/drywall",
                    domain="other.example",
                    title="Drywall Repair",
                ),
            ],
        ),
        SerpSnapshot(
            query="drywall contractor st louis",
            market_id=market.id,
            results=[
                SerpResult(
                    order=1,
                    url="https://www.same.example/contractor",
                    domain="www.same.example",
                    title="St. Louis Drywall Contractor",
                ),
            ],
        ),
    ]

    assert select_competitor_urls(snapshots, 5) == [
        "https://www.same.example/contractor",
        "https://other.example/drywall",
    ]
    enriched = enrich_competitors(
        [
            CompetitorMetric(
                url="https://www.same.example/contractor",
                domain="same.example",
            ),
            CompetitorMetric(
                url="https://other.example/drywall",
                domain="other.example",
            ),
        ],
        snapshots,
        service,
        market,
    )

    same = next(item for item in enriched if item.domain == "same.example")
    assert same.representative_query == "drywall contractor st louis"
    assert same.serp_position == 1
    assert [(item.query, item.position) for item in same.serp_observations] == [
        ("drywall contractor st louis", 1),
        ("drywall repair st louis", 4),
    ]


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
    assert competitor.formula.startswith(
        "query_and_position_exposure_weighted_mean(clamp("
    )


def test_commercial_signal_shares_scale_with_component_weight(
    tmp_path: Path,
) -> None:
    config = yaml.safe_load(Path("config/scoring.yaml").read_text())
    config["weights"]["commercial_value"] = 20
    config_path = tmp_path / "scoring.yaml"
    config_path.write_text(yaml.safe_dump(config))
    scorer = OpportunityScorer(config_path)
    full_evidence_metric = _metric().model_copy(
        update={
            "cpc": 28,
            "paid_competition": 0.8,
            "intent": "commercial",
        }
    )

    full_score = scorer.score(
        [full_evidence_metric],
        [_snapshot()],
        [],
        [_provider()],
    )
    detail = full_score.component_details["commercial_value"]

    assert detail.maximum_score == 20
    assert detail.score == 20
    assert detail.inputs["normalized_signal_shares"] == {
        "cpc": 0.5625,
        "paid_competition": 0.1875,
        "high_intent": 0.25,
    }
    assert detail.inputs["signal_point_budgets"] == {
        "cpc": 11.25,
        "paid_competition": 3.75,
        "high_intent": 5.0,
    }
    assert [
        step.inputs["maximum_points"]
        for step in detail.calculation_steps[:3]
    ] == [11.25, 3.75, 5.0]

    cpc_only_metric = full_evidence_metric.model_copy(
        update={
            "cpc": 56,
            "paid_competition": 0,
            "intent": "informational",
        }
    )
    cpc_only_score = scorer.score(
        [cpc_only_metric],
        [_snapshot()],
        [],
        [_provider()],
    )
    assert cpc_only_score.component_scores["commercial_value"] == 11.25


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


def test_top_ranked_directory_causes_more_displacement_than_rank_ten() -> None:
    scorer = OpportunityScorer()
    directory_result = _snapshot().results[0].model_copy(
        update={"classification": "directory"}
    )
    position_one = scorer.score(
        [_metric()],
        [
            _snapshot().model_copy(
                update={
                    "results": [directory_result.model_copy(update={"order": 1})],
                }
            )
        ],
        [],
        [_provider()],
    )
    position_ten = scorer.score(
        [_metric()],
        [
            _snapshot().model_copy(
                update={
                    "results": [directory_result.model_copy(update={"order": 10})],
                }
            )
        ],
        [],
        [_provider()],
    )

    assert (
        position_one.component_scores["organic_click_availability"]
        < position_ten.component_scores["organic_click_availability"]
    )
    first_share = position_one.component_details["organic_click_availability"].inputs[
        "classification_weighted_shares"
    ]["directory"]
    tenth_share = position_ten.component_details["organic_click_availability"].inputs[
        "classification_weighted_shares"
    ]["directory"]
    assert first_share > tenth_share


def test_unknown_results_receive_position_weighted_uncertainty_penalty() -> None:
    scorer = OpportunityScorer()
    known_result = _snapshot().results[0]
    unknown_result = known_result.model_copy(update={"classification": "unknown"})
    position_one = scorer.score(
        [_metric()],
        [
            _snapshot().model_copy(
                update={
                    "results": [unknown_result.model_copy(update={"order": 1})],
                }
            )
        ],
        [],
        [_provider()],
    )
    position_ten = scorer.score(
        [_metric()],
        [
            _snapshot().model_copy(
                update={
                    "results": [unknown_result.model_copy(update={"order": 10})],
                }
            )
        ],
        [],
        [_provider()],
    )
    known = scorer.score(
        [_metric()],
        [_snapshot()],
        [],
        [_provider()],
    )

    assert (
        position_one.component_scores["organic_click_availability"]
        < position_ten.component_scores["organic_click_availability"]
        < known.component_scores["organic_click_availability"]
    )
    detail = position_one.component_details["organic_click_availability"]
    assert detail.inputs["classification_penalties"]["unknown"] == 0.08
    assert detail.inputs["classification_weighted_shares"]["unknown"] > 0


def test_high_unknown_serp_share_caps_confidence() -> None:
    metric = _metric().model_copy(
        update={
            "market_granularity": "city",
            "source": "dataforseo:historical_search_volume",
        }
    )
    snapshots = [
        SerpSnapshot(
            query=f"drywall repair {query_index}",
            market_id="st-louis-mo",
            results=[
                SerpResult(
                    order=position,
                    result_type="organic",
                    url=f"https://unknown-{query_index}-{position}.example",
                    domain=f"unknown-{query_index}-{position}.example",
                    title="Ambiguous result",
                    classification="unknown",
                )
                for position in range(1, 11)
            ],
        )
        for query_index in range(3)
    ]

    score = OpportunityScorer().score(
        [metric],
        snapshots,
        _complete_competitor_sample(),
        _complete_provider_sample(),
        source_mode="live",
    )

    confidence = score.input_measurements["confidence_model"]
    assert confidence["weighted_unknown_serp_share"] == 1
    assert confidence["classification_coverage"] == 0
    assert score.confidence.value == "medium"
    assert any(
        item["factor"] == "unknown_serp_classification"
        and item["points"] == 10
        for item in confidence["deductions"]
    )
    assert any(
        "classification coverage" in item["reason"]
        for item in confidence["caps"]
    )


def test_feature_penalties_scale_with_affected_serp_share() -> None:
    scorer = OpportunityScorer()
    snapshots = [
        _snapshot().model_copy(update={"query": f"drywall repair {index}"})
        for index in range(3)
    ]
    one_affected = [
        snapshots[0].model_copy(update={"features_present": ["local_pack", "ads_top"]}),
        snapshots[1],
        snapshots[2],
    ]
    all_affected = [
        snapshot.model_copy(update={"features_present": ["local_pack", "ads_top"]})
        for snapshot in snapshots
    ]

    partial_score = scorer.score([_metric()], one_affected, [], [_provider()])
    full_score = scorer.score([_metric()], all_affected, [], [_provider()])

    assert (
        full_score.component_scores["organic_click_availability"]
        < partial_score.component_scores["organic_click_availability"]
    )
    partial_inputs = partial_score.component_details["organic_click_availability"].inputs
    assert partial_inputs["local_pack_serp_share"] == pytest.approx(1 / 3, abs=0.0001)
    assert partial_inputs["ads_top_serp_share"] == pytest.approx(1 / 3, abs=0.0001)


def test_high_value_keyword_serp_receives_more_feature_weight() -> None:
    scorer = OpportunityScorer()
    high_metric = _metric().model_copy(
        update={
            "keyword": "emergency drywall repair",
            "canonical_keyword": "emergency drywall repair",
            "search_volume": 1_000,
            "cpc": 25,
        }
    )
    low_metric = _metric().model_copy(
        update={
            "keyword": "drywall tips",
            "canonical_keyword": "drywall tips",
            "search_volume": 100,
            "cpc": 2.5,
        }
    )
    high_serp = _snapshot().model_copy(update={"query": high_metric.keyword})
    low_serp = _snapshot().model_copy(update={"query": low_metric.keyword})
    high_value_affected = scorer.score(
        [high_metric, low_metric],
        [
            high_serp.model_copy(update={"features_present": ["local_pack"]}),
            low_serp,
        ],
        [],
        [_provider()],
    )
    low_value_affected = scorer.score(
        [high_metric, low_metric],
        [
            high_serp,
            low_serp.model_copy(update={"features_present": ["local_pack"]}),
        ],
        [],
        [_provider()],
    )

    assert (
        high_value_affected.component_scores["organic_click_availability"]
        < low_value_affected.component_scores["organic_click_availability"]
    )
    high_inputs = high_value_affected.component_details[
        "organic_click_availability"
    ].inputs
    assert high_inputs["local_pack_serp_share"] == pytest.approx(0.8)
    assert [item["keyword_weight"] for item in high_inputs["serp_keyword_evidence"]] == [
        1.0,
        0.25,
    ]


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


def test_irrelevant_provider_rows_do_not_trigger_oversupply() -> None:
    suitable = [
        ProviderCandidate(
            name=f"Suitable Provider {index}",
            business_status="open",
            suitability_score=80,
        )
        for index in range(2)
    ]
    irrelevant = [
        ProviderCandidate(
            name=f"Closed Irrelevant Provider {index}",
            business_status="closed_forever",
            suitability_score=20,
        )
        for index in range(20)
    ]

    scorer = OpportunityScorer()
    baseline = scorer.score(
        [_metric()],
        [_snapshot()],
        [],
        suitable,
    )
    score = scorer.score(
        [_metric()],
        [_snapshot()],
        [],
        [*suitable, *irrelevant],
    )
    detail = score.component_details["provider_suitability"]

    assert detail.inputs["provider_count"] == 22
    assert detail.inputs["suitable_provider_count"] == 2
    assert detail.inputs["saturation_supply_count"] == 2
    assert detail.inputs["supply_count_basis"] == "suitable_provider_count"
    assert detail.inputs["supply_multiplier"] == 1
    assert detail.inputs["average_top_suitable_provider_score"] == 80
    assert detail.inputs["median_suitable_provider_score"] == 80
    assert detail.inputs["suitable_provider_share"] == 0.0909
    assert detail.inputs["raw_average_suitability_score"] == 25.45
    assert (
        score.component_scores["provider_suitability"]
        == baseline.component_scores["provider_suitability"]
    )
    assert not any(
        step.label == "Supply saturation adjustment"
        for step in detail.calculation_steps
    )


def test_provider_oversupply_uses_suitable_provider_count() -> None:
    providers = [
        ProviderCandidate(
            name=f"Suitable Provider {index}",
            business_status="open",
            suitability_score=80,
        )
        for index in range(15)
    ]

    score = OpportunityScorer().score(
        [_metric()],
        [_snapshot()],
        [],
        providers,
    )
    detail = score.component_details["provider_suitability"]

    assert detail.inputs["suitable_provider_count"] == 15
    assert detail.inputs["saturation_supply_count"] == 15
    assert detail.inputs["supply_multiplier"] == 0.72
    saturation_step = next(
        step
        for step in detail.calculation_steps
        if step.label == "Supply saturation adjustment"
    )
    assert "15 suitable providers" in saturation_step.detail


def test_missing_competitor_metrics_prevents_high_confidence() -> None:
    score = OpportunityScorer().score([_metric()], [_snapshot()], [], [_provider()])

    assert score.confidence.value != "high"
    assert "competitor_metrics" in score.missing_fields


def test_missing_cpc_has_small_configured_attractiveness_impact() -> None:
    metric, serps, competitors, providers = _rankable_evidence()
    scorer = OpportunityScorer()
    complete = scorer.score(
        [metric],
        serps,
        competitors,
        providers,
        source_mode="live",
    )
    missing_cpc = scorer.score(
        [metric.model_copy(update={"cpc": None})],
        serps,
        competitors,
        providers,
        source_mode="live",
    )

    assert complete.evidence_status == "complete"
    assert missing_cpc.evidence_status == "partial"
    assert missing_cpc.missing_data_penalties == {"keyword_cpc": 1.0}
    assert missing_cpc.score_cap is None
    assert 0 < complete.total_score - missing_cpc.total_score < 10
    for component in (
        "demand_evidence",
        "competitor_weakness",
        "organic_click_availability",
        "provider_suitability",
    ):
        assert missing_cpc.component_scores[component] == complete.component_scores[component]


def test_missing_local_demand_has_larger_confidence_than_configured_score_impact() -> None:
    metric, serps, competitors, providers = _rankable_evidence()
    scorer = OpportunityScorer()
    complete = scorer.score(
        [metric],
        serps,
        competitors,
        providers,
        source_mode="live",
    )
    national_only = scorer.score(
        [
            metric.model_copy(
                update={
                    "market_granularity": "country",
                    "search_volume": 100,
                }
            )
        ],
        serps,
        competitors,
        providers,
        source_mode="live",
    )

    assert national_only.evidence_status == "partial"
    assert national_only.missing_data_penalties == {"local_demand": 3.0}
    assert national_only.score_cap is None
    assert national_only.confidence.value == "low"
    attractiveness_impact = complete.total_score - national_only.total_score
    assert 10 < attractiveness_impact < 12
    confidence_deductions = national_only.input_measurements["confidence_model"][
        "deductions"
    ]
    confidence_impact = next(
        item["points"]
        for item in confidence_deductions
        if item["factor"] == "missing_local_demand"
    )
    assert confidence_impact == 20
    assert confidence_impact > attractiveness_impact


def test_missing_competitors_makes_full_assessment_unusable() -> None:
    metric, serps, _, providers = _rankable_evidence()
    score = OpportunityScorer().score(
        [metric],
        serps,
        [],
        providers,
        source_mode="live",
        assessment_type="full",
    )

    assert score.evidence_status == "unusable"
    assert score.missing_data_penalties == {"competitor_metrics": 8.0}
    assert score.score_cap == 40
    assert score.total_score <= 40
    assert score.confidence.value == "insufficient"


def test_missing_providers_only_removes_provider_evidence() -> None:
    metric, serps, competitors, providers = _rankable_evidence()
    scorer = OpportunityScorer()
    complete = scorer.score(
        [metric],
        serps,
        competitors,
        providers,
        source_mode="live",
    )
    missing_providers = scorer.score(
        [metric],
        serps,
        competitors,
        [],
        source_mode="live",
    )

    assert missing_providers.evidence_status == "partial"
    assert missing_providers.missing_data_penalties == {
        "provider_candidates": 4.0
    }
    assert missing_providers.score_cap is None
    assert missing_providers.component_scores["provider_suitability"] == 0
    for component in (
        "demand_evidence",
        "commercial_value",
        "competitor_weakness",
        "organic_click_availability",
    ):
        assert (
            missing_providers.component_scores[component]
            == complete.component_scores[component]
        )


def test_missing_serps_makes_full_assessment_partial_and_capped() -> None:
    metric, _, competitors, providers = _rankable_evidence()
    score = OpportunityScorer().score(
        [metric],
        [],
        competitors,
        providers,
        source_mode="live",
        assessment_type="full",
    )

    assert score.evidence_status == "partial"
    assert score.missing_data_penalties == {"serp_snapshots": 10.0}
    assert score.score_cap == 60
    assert score.total_score <= 60
    assert score.component_scores["organic_click_availability"] == 0
    assert score.confidence.value in {"low", "insufficient"}


def test_configured_missing_penalties_preserve_severity_under_global_cap() -> None:
    score = OpportunityScorer().score(
        [],
        [],
        [],
        [],
        source_mode="live",
        assessment_type="full",
    )

    assert sum(score.missing_data_penalties.values()) == 24
    assert (
        score.missing_data_penalties["serp_snapshots"]
        > score.missing_data_penalties["competitor_metrics"]
        > score.missing_data_penalties["provider_candidates"]
        > score.missing_data_penalties["local_demand"]
    )


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


def test_local_only_demand_does_not_earn_service_attractiveness_points() -> None:
    metric = _metric().model_copy(
        update={
            "search_volume": 50,
            "market_granularity": "city",
            "source": "dataforseo:historical_search_volume",
        }
    )

    score = OpportunityScorer().score(
        [metric],
        _complete_serp_sample(),
        _complete_competitor_sample(),
        _complete_provider_sample(),
        source_mode="live",
    )

    demand_inputs = score.input_measurements["demand_score_inputs"]
    assert score.input_measurements["national_service_demand"] is None
    assert score.input_measurements["provider_reported_local_demand"] == 50
    assert score.input_measurements["service_attractiveness_demand"] is None
    assert demand_inputs["service_demand_kind"] == "missing"
    assert demand_inputs["service_score"] == 0
    assert demand_inputs["market_demand_evidence_type"] == "measured_local"
    assert demand_inputs["market_threshold"] == 50
    assert demand_inputs["market_maximum_credit"] == 1
    assert demand_inputs["market_score"] == demand_inputs["market_weight"] == 8.4
    assert score.component_scores["demand_evidence"] == 8.4
    assert score.component_scores["demand_evidence"] < 24
    assert any(
        "market attractiveness only" in assumption
        for assumption in score.assumptions
    )


def test_mixed_national_and_local_demand_use_distinct_subcomponents() -> None:
    national_metric = _metric().model_copy(
        update={
            "keyword": "drywall repair national",
            "canonical_keyword": "drywall repair national",
            "search_volume": 900,
            "market_granularity": "country",
            "source": "dataforseo:historical_search_volume",
        }
    )
    local_metric = national_metric.model_copy(
        update={
            "keyword": "drywall repair local",
            "canonical_keyword": "drywall repair local",
            "market_granularity": "city",
        }
    )

    score = OpportunityScorer().score(
        [national_metric, local_metric],
        _complete_serp_sample(),
        _complete_competitor_sample(),
        _complete_provider_sample(),
        source_mode="live",
    )

    demand_inputs = score.input_measurements["demand_score_inputs"]
    assert score.input_measurements["service_attractiveness_demand"] == 900
    assert score.input_measurements["estimated_market_demand"] == 900
    assert demand_inputs["service_demand_kind"] == "provider_reported_national"
    assert demand_inputs["service_score"] == demand_inputs["service_weight"] == 15.6
    assert demand_inputs["market_score"] == demand_inputs["market_weight"] == 8.4
    assert score.component_scores["demand_evidence"] == 24


def test_national_only_demand_has_lower_confidence_despite_larger_service_weight() -> None:
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

    assert national.component_scores["demand_evidence"] == 15.6
    assert local.component_scores["demand_evidence"] == 8.4
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
    small_inputs = small.input_measurements["demand_score_inputs"]
    large_inputs = large.input_measurements["demand_score_inputs"]
    assert small_inputs["market_demand_evidence_type"] == "population_estimated"
    assert small_inputs["market_threshold"] == 15
    assert small_inputs["market_maximum_credit"] == 0.4
    assert small_inputs["market_score_cap"] == 3.36
    assert small_inputs["market_score"] == 0.5
    assert small.component_scores["demand_evidence"] == 16.1
    assert large_inputs["market_score"] == large_inputs["market_score_cap"] == 3.36
    assert large.component_scores["demand_evidence"] == 18.96
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
