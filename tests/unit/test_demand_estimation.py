from __future__ import annotations

from rank_rent.domain.models import KeywordMetric, Market
from rank_rent.services.demand import analyze_demand
from rank_rent.services.demand_estimation import (
    DemandEstimationFactor,
    MarketDemandEstimate,
    MarketDemandEstimationInputs,
)


def _national_metric(volume: int = 900) -> KeywordMetric:
    return KeywordMetric(
        keyword="water heater repair",
        canonical_keyword="water heater repair",
        intent="transactional",
        search_volume=volume,
        market_granularity="country",
        source="dataforseo:historical_search_volume",
    )


def test_population_share_estimator_is_traced_and_remains_low_confidence() -> None:
    evidence = analyze_demand(
        [_national_metric()],
        Market(
            id="market",
            display_name="Market",
            population=100_000,
            reference_population=100_000_000,
        ),
    )

    assert evidence["estimated_market_demand"] == 0.9
    assert evidence["market_estimator"] == "population_share"
    assert evidence["market_estimation_formula_version"] == "population_share_v2"
    assert evidence["market_estimation_confidence"] == "low"
    assert evidence["market_estimation_inputs"] == {
        "market_population": 100_000.0,
        "reference_population": 100_000_000.0,
        "national_service_demand": 900.0,
    }
    factors = {
        factor["name"]: factor for factor in evidence["market_estimation_factors"]
    }
    assert factors["population_share"]["value"] == 0.001
    assert all(factor["used"] for factor in factors.values())
    assert any(
        "households" in limitation
        for limitation in evidence["market_estimation_limitations"]
    )


def test_population_share_estimator_omits_estimate_without_complete_geography() -> None:
    evidence = analyze_demand(
        [_national_metric()],
        Market(id="market", display_name="Market", population=100_000),
    )

    assert evidence["estimated_market_demand"] is None
    assert evidence["market_demand_kind"] == "missing"
    assert evidence["market_estimation_confidence"] == "none"
    assert evidence["market_estimation_formula_version"] is None
    factors = {
        factor["name"]: factor for factor in evidence["market_estimation_factors"]
    }
    assert factors["market_population"]["used"] is True
    assert factors["reference_population"]["used"] is False
    assert any(
        "reference population" in limitation
        for limitation in evidence["market_estimation_limitations"]
    )


class FixedEstimator:
    name = "fixed_test_estimator"

    def estimate(
        self,
        inputs: MarketDemandEstimationInputs,
    ) -> MarketDemandEstimate:
        return MarketDemandEstimate(
            value=12.5,
            method="fixed_test_method",
            formula_version="fixed_test_v1",
            confidence="low",
            inputs=inputs.as_dict(),
            factors=(
                DemandEstimationFactor(
                    name="test_factor",
                    value=12.5,
                    unit="monthly_searches",
                    used=True,
                    source="test",
                    detail="A deterministic replacement estimator.",
                ),
            ),
            limitations=("Test-only estimator.",),
        )


def test_demand_analysis_accepts_a_replacement_market_estimator() -> None:
    evidence = analyze_demand(
        [_national_metric()],
        Market(id="market", display_name="Market"),
        estimator=FixedEstimator(),
    )

    assert evidence["estimated_market_demand"] == 12.5
    assert evidence["market_estimator"] == "fixed_test_estimator"
    assert evidence["market_estimation_method"] == "fixed_test_method"
    assert evidence["market_estimation_formula_version"] == "fixed_test_v1"
    assert evidence["market_estimation_factors"][0]["name"] == "test_factor"
