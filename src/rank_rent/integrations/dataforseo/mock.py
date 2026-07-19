from __future__ import annotations

from urllib.parse import urlparse

from rank_rent.domain.models import (
    CompetitorMetric,
    KeywordCandidate,
    KeywordMetric,
    LocationType,
    Market,
    ProviderCandidate,
    ResolvedLocation,
    SerpResult,
    SerpSnapshot,
    ServiceFamily,
    slugify,
)
from rank_rent.services.cache import RawResponseCache
from rank_rent.services.keywords import service_seed_keywords


class FixtureMarketResearchProvider:
    provider_name = "dataforseo-fixture"

    def __init__(self, cache: RawResponseCache | None = None) -> None:
        self.cache = cache

    async def resolve_location(self, query: str) -> ResolvedLocation:
        is_zip = query.strip().isdigit()
        market = Market(
            id=slugify(query),
            display_name=query if not is_zip else f"ZIP {query}",
            type=LocationType.postal_code if is_zip else LocationType.city,
            country_code="US",
            cities=[] if is_zip else [query.split(",")[0].strip()],
            postal_codes=[query] if is_zip else [],
            provider_location_code=f"mock-{slugify(query)}",
            provider_location_name=query,
            resolution_metadata={
                "original_input": query,
                "keyword_volume_granularity": "nearest_city" if is_zip else "city",
            },
        )
        notes = ["ZIP resolved to nearest supported city for keyword-volume data."] if is_zip else []
        return ResolvedLocation(
            original_input=query,
            market=market,
            provider_location_code=market.provider_location_code or "",
            provider_location_name=market.provider_location_name or query,
            granularity=market.type.value,
            notes=notes,
        )

    async def discover_keywords(
        self, service: ServiceFamily, market: Market
    ) -> list[KeywordCandidate]:
        candidates = []
        city = market.cities[0] if market.cities else market.display_name
        for query in service_seed_keywords(service):
            candidates.extend(
                [
                    KeywordCandidate(keyword=query, source="seed"),
                    KeywordCandidate(keyword=f"{query} near me", source="fixture_expansion"),
                    KeywordCandidate(keyword=f"{query} {city}", source="fixture_expansion"),
                ]
            )
        return candidates

    async def get_keyword_metrics(self, keywords: list[str], market: Market) -> list[KeywordMetric]:
        metrics: list[KeywordMetric] = []
        for index, keyword in enumerate(keywords):
            emergency = any(term in keyword for term in ["emergency", "repair", "replacement"])
            volume = max(30, 260 - index * 18)
            metrics.append(
                KeywordMetric(
                    keyword=keyword,
                    canonical_keyword=slugify(keyword).replace("-", " "),
                    intent="transactional" if emergency or "near me" in keyword else "commercial",
                    search_volume=volume,
                    cpc=18.5 if emergency else 9.25,
                    paid_competition=0.72 if emergency else 0.46,
                    monthly_history=[max(10, volume + delta) for delta in [-20, -5, 10, 25, 15, -8]],
                    source=self.provider_name,
                    market_granularity=market.type.value,
                )
            )
        return metrics

    async def get_serp_snapshot(self, keyword: str, market: Market) -> SerpSnapshot:
        city = market.cities[0] if market.cities else market.display_name
        urls = [
            f"https://{slugify(city)}trustedpros.example/{slugify(keyword)}",
            "https://www.yelp.com/search?find_desc=service",
            "https://www.homedepot.com/services",
            f"https://local-leads.example/{slugify(city)}-{slugify(keyword)}",
            f"https://{slugify(city)}familyservice.example/",
        ]
        results = []
        for order, url in enumerate(urls, start=1):
            domain = urlparse(url).netloc
            results.append(
                SerpResult(
                    order=order,
                    url=url,
                    domain=domain,
                    title=f"{keyword.title()} - result {order}",
                    description=f"Localized result for {city}",
                )
            )
        return SerpSnapshot(
            query=keyword,
            market_id=market.id,
            features_present=["ads_top", "local_pack"],
            results=results,
        )

    async def get_competitor_metrics(self, urls: list[str]) -> list[CompetitorMetric]:
        rows = []
        for index, url in enumerate(urls):
            domain = urlparse(url).netloc
            is_directory = any(name in domain for name in ["yelp", "homeadvisor", "angi"])
            rows.append(
                CompetitorMetric(
                    url=url,
                    domain=domain,
                    referring_domains=18 + index * 22 if not is_directory else 420,
                    backlinks=90 + index * 100 if not is_directory else 8000,
                    authority=21 + index * 8 if not is_directory else 82,
                    page_relevance_score=0.78 if not is_directory else 0.42,
                    local_relevance=0.85 if not is_directory else 0.25,
                    page_type="directory" if is_directory else "local_provider",
                )
            )
        return rows

    async def find_providers(
        self, service: ServiceFamily, market: Market
    ) -> list[ProviderCandidate]:
        city = market.cities[0] if market.cities else market.display_name
        return [
            ProviderCandidate(
                name=f"{city} Home Service Co",
                website=f"https://{slugify(city)}homeservice.example",
                phone="(203) 555-0101",
                contact_form_url=f"https://{slugify(city)}homeservice.example/contact",
                address=f"{city}, {market.state or 'US'}",
                service_area=market.display_name,
                category=(service.provider_categories or [service.display_name])[0],
                categories=service.provider_categories or [service.display_name],
                latitude=market.latitude,
                longitude=market.longitude,
                rating=4.6,
                review_count=87,
                business_status="open",
                contact_confidence=0.8,
            ),
            ProviderCandidate(
                name=f"County {service.display_name} Pros",
                website="https://countyservicepros.example",
                phone="(203) 555-0102",
                service_area=market.display_name,
                category=(service.provider_categories or [service.display_name])[0],
                categories=service.provider_categories or [service.display_name],
                latitude=market.latitude,
                longitude=market.longitude,
                rating=4.3,
                review_count=34,
                business_status="open",
                contact_confidence=0.55,
            ),
        ]
