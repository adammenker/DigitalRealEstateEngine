from __future__ import annotations

from statistics import mean
from typing import Any

from rank_rent.domain.models import KeywordMetric, Market

HIGH_INTENTS = {"transactional", "commercial"}


def analyze_demand(metrics: list[KeywordMetric], market: Market) -> dict[str, Any]:
    included = [metric for metric in metrics if metric.included]
    total_volume = sum(metric.search_volume or 0 for metric in included)
    granularities = sorted({metric.market_granularity or "unknown" for metric in included})
    country_level = any(item in {"country", "national"} for item in granularities)
    high_intent = [metric for metric in included if metric.intent in HIGH_INTENTS]
    history = [metric.monthly_history for metric in included if metric.monthly_history]

    estimated_market_demand: float | None = None
    estimation_method = "not_estimated"
    estimation_confidence = "none"
    population = _population(market)
    reference_population = _reference_population(market)
    if country_level and population and reference_population:
        estimated_market_demand = round(total_volume * (population / reference_population), 2)
        estimation_method = "population_share_from_country_volume"
        estimation_confidence = "low"
    elif total_volume and not country_level:
        estimated_market_demand = float(total_volume)
        estimation_method = "provider_reported_local_volume"
        estimation_confidence = "medium"

    return {
        "raw_keyword_volume": total_volume,
        "raw_volume_granularity": "mixed" if len(granularities) > 1 else (granularities[0] if granularities else "none"),
        "national_service_demand": total_volume if country_level else None,
        "estimated_market_demand": estimated_market_demand,
        "market_estimation_method": estimation_method,
        "market_estimation_confidence": estimation_confidence,
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
    value = market.resolution_metadata.get("population")
    return float(value) if isinstance(value, int | float) and value > 0 else None


def _reference_population(market: Market) -> float | None:
    for key in ("country_population", "reference_population", "national_population"):
        value = market.resolution_metadata.get(key)
        if isinstance(value, int | float) and value > 0:
            return float(value)
    return None
