from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from rank_rent.domain.models import Market

POPULATION_SHARE_ESTIMATOR = "population_share"


@dataclass(frozen=True)
class MarketDemandEstimationInputs:
    national_service_demand: float
    market_population: float | None
    reference_population: float | None

    @classmethod
    def from_market(
        cls,
        national_service_demand: float,
        market: Market,
    ) -> MarketDemandEstimationInputs:
        return cls(
            national_service_demand=national_service_demand,
            market_population=_positive_number(
                market.population,
                market.resolution_metadata.get("population"),
            ),
            reference_population=_positive_number(
                market.reference_population,
                market.resolution_metadata.get("country_population"),
                market.resolution_metadata.get("reference_population"),
                market.resolution_metadata.get("national_population"),
            ),
        )

    def as_dict(self) -> dict[str, float | None]:
        return {
            "market_population": self.market_population,
            "reference_population": self.reference_population,
            "national_service_demand": self.national_service_demand or None,
        }


@dataclass(frozen=True)
class DemandEstimationFactor:
    name: str
    value: float | None
    unit: str
    used: bool
    source: str
    detail: str


@dataclass(frozen=True)
class MarketDemandEstimate:
    value: float | None
    method: str
    formula_version: str | None
    confidence: str
    inputs: dict[str, float | None]
    factors: tuple[DemandEstimationFactor, ...]
    limitations: tuple[str, ...]

    def factors_payload(self) -> list[dict[str, Any]]:
        return [asdict(factor) for factor in self.factors]


class MarketDemandEstimator(Protocol):
    name: str

    def estimate(
        self,
        inputs: MarketDemandEstimationInputs,
    ) -> MarketDemandEstimate: ...


class PopulationShareDemandEstimator:
    name = POPULATION_SHARE_ESTIMATOR
    formula_version = "population_share_v2"

    def estimate(
        self,
        inputs: MarketDemandEstimationInputs,
    ) -> MarketDemandEstimate:
        required_factors = (
            DemandEstimationFactor(
                name="national_service_demand",
                value=inputs.national_service_demand or None,
                unit="monthly_searches",
                used=inputs.national_service_demand > 0,
                source="keyword_metrics",
                detail="Provider-reported country-level service demand.",
            ),
            DemandEstimationFactor(
                name="market_population",
                value=inputs.market_population,
                unit="people",
                used=inputs.market_population is not None,
                source="offline_us_geography",
                detail="ACS population for the selected canonical city or ZCTA.",
            ),
            DemandEstimationFactor(
                name="reference_population",
                value=inputs.reference_population,
                unit="people",
                used=inputs.reference_population is not None,
                source="offline_us_geography",
                detail="ACS U.S. population aligned to the market population vintage.",
            ),
        )
        if (
            inputs.national_service_demand <= 0
            or inputs.market_population is None
            or inputs.reference_population is None
        ):
            return MarketDemandEstimate(
                value=None,
                method="not_estimated",
                formula_version=None,
                confidence="none",
                inputs=inputs.as_dict(),
                factors=required_factors,
                limitations=(
                    "Local demand is unavailable because national demand, market population, "
                    "and reference population were not all present.",
                ),
            )

        population_share = inputs.market_population / inputs.reference_population
        value = round(inputs.national_service_demand * population_share, 2)
        factors = (
            *required_factors,
            DemandEstimationFactor(
                name="population_share",
                value=round(population_share, 8),
                unit="ratio",
                used=True,
                source=self.formula_version,
                detail="Market population divided by aligned U.S. reference population.",
            ),
        )
        return MarketDemandEstimate(
            value=value,
            method="population_share_from_country_volume",
            formula_version=self.formula_version,
            confidence="low",
            inputs=inputs.as_dict(),
            factors=factors,
            limitations=(
                "Population-share estimation assumes service demand is distributed "
                "proportionally.",
                "The estimate does not yet adjust for households, housing units, "
                "homeownership, housing age, climate, local intent, income, or competition.",
            ),
        )


def build_market_demand_estimator(name: str | None) -> MarketDemandEstimator:
    normalized = (name or POPULATION_SHARE_ESTIMATOR).strip().lower()
    if normalized == POPULATION_SHARE_ESTIMATOR:
        return PopulationShareDemandEstimator()
    raise ValueError(f"Unsupported market demand estimator: {name}")


def _positive_number(*values: object) -> float | None:
    for value in values:
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            return float(value)
    return None
