from __future__ import annotations

from urllib.parse import urlparse

from rank_rent.domain.models import (
    CompetitorMetric,
    CompetitorSerpObservation,
    Market,
    SerpResult,
    SerpSnapshot,
    ServiceFamily,
    slugify,
)

COMPETITOR_ARCHETYPES = {
    "directory",
    "marketplace",
    "lead_generator",
    "national_brand",
    "local_provider",
    "informational_publisher",
    "government_or_nonprofit",
}


def select_competitor_urls(
    serp_snapshots: list[SerpSnapshot],
    limit: int,
) -> list[str]:
    observations = _serp_observations_by_domain(serp_snapshots)
    representatives = [
        min(domain_observations, key=_observation_sort_key)
        for domain_observations in observations.values()
    ]
    representatives.sort(key=_observation_sort_key)
    return [observation.url for observation in representatives[:limit]]


def enrich_competitors(
    competitors: list[CompetitorMetric],
    serp_snapshots: list[SerpSnapshot],
    service: ServiceFamily,
    market: Market,
) -> list[CompetitorMetric]:
    observations_by_domain = _serp_observations_by_domain(serp_snapshots)
    result_by_observation = {
        (snapshot.query, result.order, result.url): result
        for snapshot in serp_snapshots
        for result in snapshot.results
        if result.result_type == "organic"
    }
    service_tokens = set(_tokens(service.display_name))
    market_tokens = set(_tokens(market.display_name))
    market_tokens.update(token for city in market.cities for token in _tokens(city))
    output: list[CompetitorMetric] = []
    seen_domains: set[str] = set()
    for competitor in competitors:
        domain = _normalize_domain(
            competitor.domain or urlparse(competitor.url).netloc
        )
        if not domain or domain in seen_domains:
            continue
        seen_domains.add(domain)
        observations = observations_by_domain.get(domain, [])
        representative = (
            min(observations, key=_observation_sort_key) if observations else None
        )
        result = (
            result_by_observation.get(
                (
                    representative.query,
                    representative.position,
                    representative.url,
                )
            )
            if representative
            else None
        )
        text = " ".join(
            [
                competitor.url,
                competitor.domain,
                result.title if result else "",
                result.description if result else "",
            ]
        )
        tokens = set(_tokens(text))
        service_match = len(tokens & service_tokens) / max(1, len(service_tokens))
        market_match = len(tokens & market_tokens) / max(1, len(market_tokens))
        page_type = result.classification if result else competitor.page_type
        if competitor.page_type != "unknown" and page_type == "unknown":
            page_type = competitor.page_type
        relevance = round(max(competitor.page_relevance_score or 0, service_match), 3)
        local = round(max(competitor.local_relevance or 0, market_match), 3)
        archetype = page_type if page_type in COMPETITOR_ARCHETYPES else "unknown"
        signals = {
            "serp_classification": result.classification if result else None,
            "competitor_archetype": archetype,
            "service_token_match": round(service_match, 3),
            "market_token_match": round(market_match, 3),
            "is_directory_aggregator": archetype == "directory",
            "is_marketplace": archetype == "marketplace",
            "is_lead_generator": archetype == "lead_generator",
            "is_national_service_brand": archetype == "national_brand",
            "classification_confidence": result.classification_confidence if result else None,
        }
        output.append(
            competitor.model_copy(
                update={
                    "page_type": page_type,
                    "page_relevance_score": relevance,
                    "local_relevance": local,
                    "relevance_signals": signals,
                    "representative_query": (
                        representative.query if representative else None
                    ),
                    "serp_position": (
                        representative.position if representative else None
                    ),
                    "serp_observations": observations,
                }
            )
        )
    return output


def _tokens(value: str) -> list[str]:
    return slugify(value).replace("-", " ").split()


def _normalize_domain(domain: str) -> str:
    return domain.lower().removeprefix("www.").strip()


def _serp_observations_by_domain(
    serp_snapshots: list[SerpSnapshot],
) -> dict[str, list[CompetitorSerpObservation]]:
    by_domain: dict[str, list[CompetitorSerpObservation]] = {}
    seen: set[tuple[str, str, int, str]] = set()
    for snapshot in serp_snapshots:
        for result in snapshot.results:
            if result.result_type != "organic":
                continue
            domain = _result_domain(result)
            if not domain:
                continue
            key = (domain, snapshot.query, result.order, result.url)
            if key in seen:
                continue
            seen.add(key)
            by_domain.setdefault(domain, []).append(
                CompetitorSerpObservation(
                    query=snapshot.query,
                    position=result.order,
                    url=result.url,
                )
            )
    for observations in by_domain.values():
        observations.sort(key=_observation_sort_key)
    return by_domain


def _result_domain(result: SerpResult) -> str:
    return _normalize_domain(result.domain or urlparse(result.url).netloc)


def _observation_sort_key(
    observation: CompetitorSerpObservation,
) -> tuple[int, str, str]:
    return (observation.position, observation.query, observation.url)
