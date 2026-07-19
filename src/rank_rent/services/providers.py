from __future__ import annotations

import math
from typing import Any

from rank_rent.domain.models import Market, ProviderCandidate, ServiceFamily, slugify


def score_provider_suitability(
    providers: list[ProviderCandidate],
    service: ServiceFamily,
    market: Market,
    config: dict[str, Any],
) -> list[ProviderCandidate]:
    return [_score_provider(provider, service, market, config) for provider in providers]


def provider_suitability_summary(
    providers: list[ProviderCandidate],
    config: dict[str, Any],
) -> dict[str, object]:
    threshold = float(config["suitable_threshold"])
    usable = [
        provider
        for provider in providers
        if (provider.suitability_score or 0) >= threshold
        and _status_value(provider.business_status, config) > 0
    ]
    scores = [provider.suitability_score or 0 for provider in providers]
    signal_names = list(config["signal_weights"])
    return {
        "provider_count": len(providers),
        "suitable_provider_count": len(usable),
        "suitable_threshold": threshold,
        "average_suitability_score": round(sum(scores) / max(1, len(scores)), 2),
        "average_signal_scores": {
            signal: round(
                sum(
                    float(provider.suitability_signals.get(signal, {}).get("normalized", 0))
                    for provider in providers
                )
                / max(1, len(providers)),
                3,
            )
            for signal in signal_names
        },
        "top_providers": [
            {
                "name": provider.name,
                "website": provider.website,
                "phone": provider.phone,
                "score": provider.suitability_score,
                "reasons": provider.suitability_reasons,
                "signals": provider.suitability_signals,
            }
            for provider in sorted(
                providers,
                key=lambda item: item.suitability_score or 0,
                reverse=True,
            )[:5]
        ],
    }


def _score_provider(
    provider: ProviderCandidate,
    service: ServiceFamily,
    market: Market,
    config: dict[str, Any],
) -> ProviderCandidate:
    weights = {
        name: max(0.0, float(value))
        for name, value in config["signal_weights"].items()
    }
    signals = {
        "service_fit": _service_fit(provider, service),
        "geographic_fit": _geographic_fit(provider, market, config),
        "status_certainty": _status_fit(provider, config),
        "contactability": _contactability(provider, config),
        "reputation": _reputation(provider, config),
    }
    total = sum(
        float(signal["normalized"]) * weights.get(name, 0)
        for name, signal in signals.items()
    )
    reasons = [
        f"{name}:{float(signal['normalized']):.2f}:{signal['method']}"
        for name, signal in signals.items()
    ]
    if float(signals["status_certainty"]["normalized"]) <= 0:
        total = min(total, float(config["inactive_score_cap"]))
        reasons.append("inactive_status_cap_applied")
    for name, signal in signals.items():
        signal["weight"] = weights.get(name, 0)
        signal["weighted_score"] = round(
            float(signal["normalized"]) * weights.get(name, 0),
            2,
        )

    return provider.model_copy(
        update={
            "suitability_score": round(min(max(total, 0), 100), 2),
            "suitability_reasons": reasons,
            "suitability_signals": signals,
        }
    )


def _service_fit(
    provider: ProviderCandidate,
    service: ServiceFamily,
) -> dict[str, Any]:
    provider_categories = _unique_strings([provider.category or "", *provider.categories])
    configured_categories = _unique_strings(service.provider_categories)
    exact_matches = sorted(
        {
            category
            for category in provider_categories
            if _normalized_phrase(category)
            in {_normalized_phrase(expected) for expected in configured_categories}
        }
    )
    if exact_matches:
        return {
            "normalized": 1.0,
            "method": "configured_provider_category_exact",
            "matched_categories": exact_matches,
            "provider_categories": provider_categories,
            "configured_categories": configured_categories,
        }

    configured_similarity = max(
        (
            _token_coverage(provider_category, expected)
            for provider_category in provider_categories
            for expected in configured_categories
        ),
        default=0.0,
    )
    service_labels = [
        service.display_name,
        *service.seed_queries,
        *configured_categories,
    ]
    provider_text = " ".join([provider.name, *provider_categories])
    fallback_similarity = max(
        (_token_coverage(provider_text, label) for label in service_labels),
        default=0.0,
    )
    normalized = max(configured_similarity, fallback_similarity * 0.65)
    method = (
        "configured_provider_category_overlap"
        if configured_similarity >= fallback_similarity * 0.65
        and configured_similarity > 0
        else "service_language_overlap"
        if fallback_similarity > 0
        else "no_service_evidence"
    )
    return {
        "normalized": round(min(normalized, 1), 3),
        "method": method,
        "configured_category_similarity": round(configured_similarity, 3),
        "service_language_similarity": round(fallback_similarity, 3),
        "provider_categories": provider_categories,
        "configured_categories": configured_categories,
    }


def _geographic_fit(
    provider: ProviderCandidate,
    market: Market,
    config: dict[str, Any],
) -> dict[str, Any]:
    geography = config["geography"]
    candidates: list[tuple[float, str]] = []
    distance_km = _distance_km(
        provider.latitude,
        provider.longitude,
        market.latitude,
        market.longitude,
    )
    if distance_km is not None:
        full_credit = float(geography["full_credit_distance_km"])
        maximum = max(full_credit, float(geography["max_distance_km"]))
        if distance_km <= full_credit:
            distance_score = 1.0
        elif distance_km >= maximum:
            distance_score = 0.0
        else:
            distance_score = 1 - ((distance_km - full_credit) / (maximum - full_credit))
        candidates.append((distance_score, "coordinate_distance"))

    service_area_match = _market_text_match(provider.service_area or "", market)
    if service_area_match:
        candidates.append(
            (
                service_area_match * float(geography["service_area_match_score"]),
                "verified_service_area",
            )
        )
    address_match = _market_text_match(provider.address or "", market)
    if address_match:
        candidates.append(
            (
                address_match * float(geography["address_match_score"]),
                "listing_address",
            )
        )

    normalized, method = max(candidates, default=(0.0, "no_geographic_evidence"))
    return {
        "normalized": round(min(max(normalized, 0), 1), 3),
        "method": method,
        "distance_km": round(distance_km, 2) if distance_km is not None else None,
        "service_area_match": round(service_area_match, 3),
        "address_match": round(address_match, 3),
        "provider_coordinates": [provider.latitude, provider.longitude]
        if provider.latitude is not None and provider.longitude is not None
        else None,
        "market_coordinates": [market.latitude, market.longitude]
        if market.latitude is not None and market.longitude is not None
        else None,
    }


def _status_fit(
    provider: ProviderCandidate,
    config: dict[str, Any],
) -> dict[str, Any]:
    normalized_status = provider.business_status.strip().lower() or "unknown"
    normalized = _status_value(normalized_status, config)
    if normalized >= 1:
        method = "confirmed_operating"
    elif normalized > 0:
        method = "status_uncertain"
    else:
        method = "inactive_or_closed"
    return {
        "normalized": round(normalized, 3),
        "method": method,
        "reported_status": normalized_status,
    }


def _status_value(status: str, config: dict[str, Any]) -> float:
    values = config["status_scores"]
    normalized_status = status.strip().lower() or "unknown"
    return min(
        max(
            float(values.get(normalized_status, values.get("unknown", 0))),
            0,
        ),
        1,
    )


def _contactability(
    provider: ProviderCandidate,
    config: dict[str, Any],
) -> dict[str, Any]:
    contact = config["contactability"]
    channels = {
        "website": bool(provider.website),
        "phone": bool(provider.phone),
        "email": bool(provider.email),
        "contact_form": bool(provider.contact_form_url),
    }
    channel_strength = max(
        (
            float(contact["channel_strengths"][channel])
            for channel, available in channels.items()
            if available
        ),
        default=0.0,
    )
    confidence = (
        min(max(provider.contact_confidence, 0), 1)
        if provider.contact_confidence is not None
        else float(contact["unknown_confidence"])
    )
    confidence_floor = min(max(float(contact["confidence_floor"]), 0), 1)
    reliability_multiplier = confidence_floor + ((1 - confidence_floor) * confidence)
    normalized = channel_strength * reliability_multiplier
    return {
        "normalized": round(min(normalized, 1), 3),
        "method": "strongest_channel_adjusted_by_confidence"
        if channels and channel_strength
        else "no_contact_channel",
        "channels": channels,
        "channel_strength": round(channel_strength, 3),
        "contact_confidence": round(confidence, 3),
        "reliability_multiplier": round(reliability_multiplier, 3),
    }


def _reputation(
    provider: ProviderCandidate,
    config: dict[str, Any],
) -> dict[str, Any]:
    reputation = config["reputation"]
    rating_score = (
        min(max(provider.rating, 0), 5) / 5 if provider.rating is not None else 0
    )
    review_score = (
        min(max(provider.review_count, 0), int(reputation["review_saturation_count"]))
        / max(1, int(reputation["review_saturation_count"]))
        if provider.review_count is not None
        else 0
    )
    normalized = (
        rating_score * float(reputation["rating_share"])
        + review_score * float(reputation["review_count_share"])
    )
    return {
        "normalized": round(min(normalized, 1), 3),
        "method": "rating_and_review_evidence"
        if provider.rating is not None or provider.review_count is not None
        else "no_reputation_evidence",
        "rating": provider.rating,
        "review_count": provider.review_count,
        "rating_normalized": round(rating_score, 3),
        "review_count_normalized": round(review_score, 3),
    }


def _distance_km(
    latitude: float | None,
    longitude: float | None,
    market_latitude: float | None,
    market_longitude: float | None,
) -> float | None:
    if None in {latitude, longitude, market_latitude, market_longitude}:
        return None
    assert latitude is not None
    assert longitude is not None
    assert market_latitude is not None
    assert market_longitude is not None
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        [latitude, longitude, market_latitude, market_longitude],
    )
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(math.sqrt(haversine))


def _market_text_match(value: str, market: Market) -> float:
    normalized = _normalized_phrase(value)
    if not normalized:
        return 0.0
    for postal_code in market.postal_codes:
        if _normalized_phrase(postal_code) in normalized:
            return 1.0
    for city in market.cities:
        if _normalized_phrase(city) in normalized:
            return 1.0
    display = _normalized_phrase(market.display_name)
    if display and display in normalized:
        return 1.0
    return 0.0


def _token_coverage(value: str, expected: str) -> float:
    value_tokens = _meaningful_tokens(value)
    expected_tokens = _meaningful_tokens(expected)
    if not value_tokens or not expected_tokens:
        return 0.0
    return len(value_tokens & expected_tokens) / len(expected_tokens)


def _meaningful_tokens(value: str) -> set[str]:
    ignored = {
        "and",
        "company",
        "contractor",
        "contractors",
        "service",
        "services",
        "the",
    }
    return {
        token
        for token in _normalized_phrase(value).split()
        if len(token) > 1 and token not in ignored
    }


def _normalized_phrase(value: str) -> str:
    return slugify(value).replace("-", " ")


def _unique_strings(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return output
