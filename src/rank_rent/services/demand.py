from __future__ import annotations

from statistics import mean
from typing import Any

from rank_rent.domain.models import KeywordMetric, Market

HIGH_INTENTS = {"transactional", "commercial"}
NATIONAL_GRANULARITIES = {"country", "national"}
LOCAL_GRANULARITIES = {"city", "postal_code", "county", "metro", "market", "local"}


def analyze_demand(metrics: list[KeywordMetric], market: Market) -> dict[str, Any]:
    included = [metric for metric in metrics if metric.included]
    total_volume = sum(metric.search_volume or 0 for metric in included)
    granularities = sorted({metric.market_granularity or "unknown" for metric in included})
    volume_by_granularity = _volume_by_granularity(included)
    national_volume = sum(
        volume for granularity, volume in volume_by_granularity.items()
        if granularity in NATIONAL_GRANULARITIES
    )
    provider_local_volume = sum(
        volume for granularity, volume in volume_by_granularity.items()
        if granularity in LOCAL_GRANULARITIES
    )
    high_intent = [metric for metric in included if metric.intent in HIGH_INTENTS]
    history = [metric.monthly_history for metric in included if metric.monthly_history]

    estimated_market_demand: float | None = None
    estimation_method = "not_estimated"
    estimation_confidence = "none"
    market_demand_kind = "missing"
    estimation_limitations: list[str] = []
    population = _population(market)
    reference_population = _reference_population(market)
    if provider_local_volume:
        estimated_market_demand = float(provider_local_volume)
        estimation_method = "provider_reported_local_volume"
        estimation_confidence = _provider_local_confidence(granularities)
        market_demand_kind = "measured_local"
    elif national_volume and population and reference_population:
        estimated_market_demand = round(national_volume * (population / reference_population), 2)
        estimation_method = "population_share_from_country_volume"
        estimation_confidence = "low"
        market_demand_kind = "estimated_local"
        estimation_limitations.append(
            "Population-share estimation assumes service demand is distributed proportionally "
            "and does not account for local intent, income, housing stock, or competition."
        )
    elif national_volume:
        estimation_limitations.append(
            "Local demand is unavailable because market and reference population metadata "
            "were not both present."
        )

    return {
        "raw_keyword_volume": total_volume,
        "raw_volume_granularity": "mixed" if len(granularities) > 1 else (granularities[0] if granularities else "none"),
        "raw_volume_by_granularity": volume_by_granularity,
        "national_service_demand": national_volume or None,
        "provider_reported_local_demand": provider_local_volume or None,
        "service_attractiveness_demand": national_volume or provider_local_volume or None,
        "estimated_market_demand": estimated_market_demand,
        "market_demand_kind": market_demand_kind,
        "market_estimation_method": estimation_method,
        "market_estimation_confidence": estimation_confidence,
        "market_estimation_formula_version": "population_share_v1" if market_demand_kind == "estimated_local" else None,
        "market_estimation_inputs": {
            "market_population": population,
            "reference_population": reference_population,
            "national_service_demand": national_volume or None,
        },
        "market_estimation_limitations": estimation_limitations,
        "high_intent_keyword_count": len(high_intent),
        "high_intent_share": round(len(high_intent) / max(1, len(included)), 3),
        "clustered_demand": [
            {
                "keyword": metric.keyword,
                "canonical_keyword": metric.canonical_keyword,
                "volume": metric.search_volume,
                "intent": metric.intent,
                "cpc": metric.cpc,
            }
            for metric in included
        ],
        "seasonality": _seasonality(history),
        "demand_source": sorted({metric.source for metric in included}),
    }


def _volume_by_granularity(metrics: list[KeywordMetric]) -> dict[str, int]:
    output: dict[str, int] = {}
    for metric in metrics:
        granularity = (metric.market_granularity or "unknown").lower()
        output[granularity] = output.get(granularity, 0) + (metric.search_volume or 0)
    return dict(sorted(output.items()))


def _provider_local_confidence(granularities: list[str]) -> str:
    local = set(granularities) & LOCAL_GRANULARITIES
    if local and local <= {"city", "postal_code", "local"}:
        return "high"
    return "medium"


def _seasonality(history: list[list[int]]) -> dict[str, Any]:
    if not history:
        return {"available": False, "peak_to_average_ratio": None, "monthly_average": None}
    month_count = max(len(row) for row in history)
    totals = []
    for index in range(month_count):
        totals.append(sum(row[index] for row in history if index < len(row)))
    monthly_average = mean(totals) if totals else 0
    peak_ratio = max(totals) / monthly_average if monthly_average else None
    return {
        "available": True,
        "peak_to_average_ratio": round(peak_ratio, 3) if peak_ratio else None,
        "monthly_average": round(monthly_average, 2),
        "months_observed": len(totals),
    }


def _population(market: Market) -> float | None:
    if market.population is not None and market.population > 0:
        return float(market.population)
    value = market.resolution_metadata.get("population")
    return float(value) if isinstance(value, int | float) and value > 0 else None


def _reference_population(market: Market) -> float | None:
    if market.reference_population is not None and market.reference_population > 0:
        return float(market.reference_population)
    for key in ("country_population", "reference_population", "national_population"):
        value = market.resolution_metadata.get(key)
        if isinstance(value, int | float) and value > 0:
            return float(value)
    return None
