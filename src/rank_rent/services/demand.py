from __future__ import annotations

from statistics import mean
from typing import Any

from rank_rent.domain.models import KeywordMetric, Market
from rank_rent.services.demand_estimation import (
    MarketDemandEstimationInputs,
    MarketDemandEstimator,
    PopulationShareDemandEstimator,
)

HIGH_INTENTS = {"transactional", "commercial"}
NATIONAL_GRANULARITIES = {"country", "national"}
LOCAL_GRANULARITIES = {"city", "postal_code", "county", "metro", "market", "local"}


def analyze_demand(
    metrics: list[KeywordMetric],
    market: Market,
    estimator: MarketDemandEstimator | None = None,
) -> dict[str, Any]:
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

    estimator = estimator or PopulationShareDemandEstimator()
    estimation = estimator.estimate(
        MarketDemandEstimationInputs.from_market(float(national_volume), market)
    )
    estimated_market_demand = estimation.value
    estimation_method = estimation.method
    estimation_confidence = estimation.confidence
    market_demand_kind = "missing"
    if provider_local_volume:
        estimated_market_demand = float(provider_local_volume)
        estimation_method = "provider_reported_local_volume"
        estimation_confidence = _provider_local_confidence(granularities)
        market_demand_kind = "measured_local"
    elif estimated_market_demand is not None:
        market_demand_kind = "estimated_local"

    return {
        "raw_keyword_volume": total_volume,
        "raw_volume_granularity": "mixed" if len(granularities) > 1 else (granularities[0] if granularities else "none"),
        "raw_volume_by_granularity": volume_by_granularity,
        "national_service_demand": national_volume or None,
        "provider_reported_local_demand": provider_local_volume or None,
        "service_attractiveness_demand": national_volume or None,
        "service_demand_kind": "provider_reported_national" if national_volume else "missing",
        "estimated_market_demand": estimated_market_demand,
        "market_demand_kind": market_demand_kind,
        "market_estimation_method": estimation_method,
        "market_estimation_confidence": estimation_confidence,
        "market_estimator": estimator.name,
        "market_estimation_formula_version": (
            estimation.formula_version if market_demand_kind == "estimated_local" else None
        ),
        "market_estimation_inputs": estimation.inputs,
        "market_estimation_factors": (
            []
            if market_demand_kind == "measured_local"
            else estimation.factors_payload()
        ),
        "market_estimation_limitations": (
            list(estimation.limitations)
            if market_demand_kind in {"estimated_local", "missing"}
            else []
        ),
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
