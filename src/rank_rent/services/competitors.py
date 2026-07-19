from __future__ import annotations

from urllib.parse import urlparse

from rank_rent.domain.models import CompetitorMetric, Market, SerpSnapshot, ServiceFamily, slugify

COMPETITOR_ARCHETYPES = {
    "directory",
    "marketplace",
    "lead_generator",
    "national_brand",
    "local_provider",
    "informational_publisher",
    "government_or_nonprofit",
}


def enrich_competitors(
    competitors: list[CompetitorMetric],
    serp_snapshots: list[SerpSnapshot],
    service: ServiceFamily,
    market: Market,
) -> list[CompetitorMetric]:
    serp_by_domain = {
        _normalize_domain(result.domain or urlparse(result.url).netloc): result
        for snapshot in serp_snapshots
        for result in snapshot.results
    }
    service_tokens = set(_tokens(service.display_name))
    market_tokens = set(_tokens(market.display_name))
    market_tokens.update(token for city in market.cities for token in _tokens(city))
    output: list[CompetitorMetric] = []
    for competitor in competitors:
        domain = _normalize_domain(competitor.domain)
        result = serp_by_domain.get(domain)
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
                }
            )
        )
    return output


def _tokens(value: str) -> list[str]:
    return slugify(value).replace("-", " ").split()


def _normalize_domain(domain: str) -> str:
    return domain.lower().removeprefix("www.").strip()
