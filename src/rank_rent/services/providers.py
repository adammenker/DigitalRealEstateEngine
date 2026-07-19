from __future__ import annotations

from rank_rent.domain.models import Market, ProviderCandidate, ServiceFamily, slugify

CLOSED_STATUSES = {"closed", "closed_forever", "temporarily_closed", "permanently_closed"}


def score_provider_suitability(
    providers: list[ProviderCandidate],
    service: ServiceFamily,
    market: Market,
) -> list[ProviderCandidate]:
    return [_score_provider(provider, service, market) for provider in providers]


def provider_suitability_summary(providers: list[ProviderCandidate]) -> dict[str, object]:
    usable = [
        provider
        for provider in providers
        if (provider.suitability_score or 0) >= 55
        and provider.business_status.lower() not in CLOSED_STATUSES
    ]
    scores = [provider.suitability_score or 0 for provider in providers]
    return {
        "provider_count": len(providers),
        "suitable_provider_count": len(usable),
        "average_suitability_score": round(sum(scores) / max(1, len(scores)), 2),
        "top_providers": [
            {
                "name": provider.name,
                "website": provider.website,
                "phone": provider.phone,
                "score": provider.suitability_score,
                "reasons": provider.suitability_reasons,
            }
            for provider in sorted(providers, key=lambda item: item.suitability_score or 0, reverse=True)[
                :5
            ]
        ],
    }


def _score_provider(
    provider: ProviderCandidate,
    service: ServiceFamily,
    market: Market,
) -> ProviderCandidate:
    score = 0.0
    reasons: list[str] = []
    status = provider.business_status.lower()
    if status in CLOSED_STATUSES:
        reasons.append("business_status_closed")
    else:
        score += 18
        reasons.append("business_open_or_unknown")

    if provider.website:
        score += 18
        reasons.append("has_website")
    if provider.phone:
        score += 14
        reasons.append("has_phone")
    if provider.email or provider.contact_form_url:
        score += 10
        reasons.append("has_direct_contact")
    if provider.rating is not None:
        rating_points = min(max(provider.rating, 0), 5) / 5 * 14
        score += rating_points
        reasons.append(f"rating:{provider.rating}")
    if provider.review_count:
        score += min(provider.review_count, 100) / 100 * 8
        reasons.append(f"reviews:{provider.review_count}")
    if _service_match(provider, service):
        score += 10
        reasons.append("category_matches_service")
    if _market_match(provider, market):
        score += 8
        reasons.append("located_or_serves_market")
    if provider.contact_confidence is not None:
        score += min(max(provider.contact_confidence, 0), 1) * 8
        reasons.append(f"contact_confidence:{provider.contact_confidence}")

    return provider.model_copy(
        update={
            "suitability_score": round(min(score, 100), 2),
            "suitability_reasons": reasons,
        }
    )


def _service_match(provider: ProviderCandidate, service: ServiceFamily) -> bool:
    text = " ".join([provider.category or "", provider.name])
    service_tokens = set(slugify(service.display_name).replace("-", " ").split())
    provider_tokens = set(slugify(text).replace("-", " ").split())
    return bool(service_tokens & provider_tokens)


def _market_match(provider: ProviderCandidate, market: Market) -> bool:
    text = " ".join([provider.address or "", provider.service_area or "", provider.name])
    market_tokens = set(slugify(market.display_name).replace("-", " ").split())
    market_tokens.update(
        token for city in market.cities for token in slugify(city).replace("-", " ").split()
    )
    provider_tokens = set(slugify(text).replace("-", " ").split())
    return bool(market_tokens & provider_tokens)
