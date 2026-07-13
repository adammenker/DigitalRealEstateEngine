import asyncio

from rank_rent.domain.models import KeywordCandidate, Market, SerpResult, ServiceFamily
from rank_rent.integrations.domain_availability.mock import MockDomainAvailabilityProvider
from rank_rent.scoring.serp import classify_result
from rank_rent.services.domains import generate_domain_candidates
from rank_rent.services.keywords import dedupe_and_filter_keywords


def test_keyword_dedupe_and_negative_filter() -> None:
    result = dedupe_and_filter_keywords(
        [
            KeywordCandidate(keyword="Water Heater Repair"),
            KeywordCandidate(keyword="water heater repair"),
            KeywordCandidate(keyword="water heater repair jobs"),
        ],
        ["jobs"],
    )
    assert len(result) == 2
    assert result[0].included is True
    assert result[1].included is False
    assert result[1].excluded_reason == "negative_term"


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
