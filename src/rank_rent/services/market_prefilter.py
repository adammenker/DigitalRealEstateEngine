from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.services.locations import (
    LocationCandidate,
    location_candidate_from_geography_record,
)
from rank_rent.services.us_geography import USGeographyIndex, USGeographyRecord
from rank_rent.settings import Settings

SUPPORTED_SIGNALS = {
    "population",
    "households",
    "housing_units",
    "owner_occupied_units",
    "homeownership_rate",
    "housing_age",
    "household_density",
}


class MarketPrefilterAssessment(BaseModel):
    assessment_version: str
    config_hash: str
    geography_dataset_version: str
    service_profile: str
    location: LocationCandidate
    score: float = Field(ge=0, le=100)
    rank: int | None = None
    recommendation: str
    confidence: str
    component_scores: dict[str, float] = Field(default_factory=dict)
    input_signals: dict[str, Any] = Field(default_factory=dict)
    missing_signals: list[str] = Field(default_factory=list)
    explanation: str


class MarketPrefilterProfile(BaseModel):
    match_terms: list[str] = Field(default_factory=list)
    signal_weights: dict[str, float]
    strong_values: dict[str, float]

    @model_validator(mode="after")
    def validate_signal_contract(self) -> MarketPrefilterProfile:
        unknown_signals = set(self.signal_weights) - SUPPORTED_SIGNALS
        if unknown_signals:
            names = ", ".join(sorted(unknown_signals))
            raise ValueError(f"Unsupported prefilter signals: {names}.")
        if set(self.signal_weights) != set(self.strong_values):
            raise ValueError(
                "signal_weights and strong_values must contain identical signal names."
            )
        if not self.signal_weights or sum(self.signal_weights.values()) <= 0:
            raise ValueError("A prefilter profile must have a positive signal weight.")
        if any(weight < 0 for weight in self.signal_weights.values()):
            raise ValueError("Prefilter signal weights cannot be negative.")
        if any(value <= 0 for value in self.strong_values.values()):
            raise ValueError("Prefilter strong values must be positive.")
        return self


class RecommendationThresholds(BaseModel):
    advance_to_testing: float = Field(ge=0, le=100)
    review: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_order(self) -> RecommendationThresholds:
        if self.review >= self.advance_to_testing:
            raise ValueError("review must be lower than advance_to_testing.")
        return self


class MarketPrefilterConfig(BaseModel):
    version: str
    minimum_population: int = Field(ge=1)
    maximum_results: int = Field(ge=1, le=500)
    recommendation_thresholds: RecommendationThresholds
    profiles: dict[str, MarketPrefilterProfile]

    @model_validator(mode="after")
    def validate_profiles(self) -> MarketPrefilterConfig:
        if "generic_local_service" not in self.profiles:
            raise ValueError("generic_local_service is required as the fallback profile.")
        return self


class MarketPrefilter:
    def __init__(
        self,
        index: USGeographyIndex,
        config_path: Path = Path("config/market_prefilter.yaml"),
    ) -> None:
        raw_config = config_path.read_text()
        self.config = MarketPrefilterConfig.model_validate(yaml.safe_load(raw_config))
        self.config_hash = hashlib.sha256(raw_config.encode("utf-8")).hexdigest()[:16]
        self.index = index

    @classmethod
    def from_settings(cls, settings: Settings) -> MarketPrefilter:
        config_path = settings.project_root / "config/market_prefilter.yaml"
        return cls(USGeographyIndex.from_settings(settings), config_path)

    def rank_markets(
        self,
        service: ServiceFamily,
        *,
        states: list[str] | None = None,
        geography_kind: str = "city",
        limit: int = 20,
        minimum_population: int | None = None,
    ) -> tuple[list[MarketPrefilterAssessment], int]:
        configured_limit = self.config.maximum_results
        bounded_limit = max(1, min(limit, configured_limit))
        population_floor = (
            int(minimum_population)
            if minimum_population is not None
            else self.config.minimum_population
        )
        records = self.index.list_markets(
            kind=geography_kind,
            states=states or [],
            minimum_population=max(1, population_floor),
        )
        assessments = [self.assess_record(service, record) for record in records]
        assessments.sort(
            key=lambda item: (
                item.score,
                item.input_signals.get("households") or 0,
                item.location.population,
                item.location.label,
            ),
            reverse=True,
        )
        selected = assessments[:bounded_limit]
        for rank, assessment in enumerate(selected, start=1):
            assessment.rank = rank
        return selected, len(assessments)

    def assess_market(
        self,
        service: ServiceFamily,
        market: Market,
    ) -> MarketPrefilterAssessment | None:
        if not market.geography_id:
            return None
        record = self.index.get(market.geography_id)
        return self.assess_record(service, record) if record is not None else None

    def assess_record(
        self,
        service: ServiceFamily,
        record: USGeographyRecord,
    ) -> MarketPrefilterAssessment:
        profile_name, profile = self.profile_for_service(service)
        signals = _signals(record)
        weights = {
            name: max(0.0, value)
            for name, value in profile.signal_weights.items()
        }
        total_weight = sum(weights.values())
        normalized_weights = {
            name: weight / total_weight if total_weight > 0 else 0.0
            for name, weight in weights.items()
        }
        component_scores: dict[str, float] = {}
        missing_signals: list[str] = []
        for signal, weight in normalized_weights.items():
            value = signals.get(signal)
            if value is None:
                missing_signals.append(signal)
                component_scores[signal] = 0.0
                continue
            normalized = _normalize_signal(
                signal,
                float(value),
                profile.strong_values[signal],
            )
            component_scores[signal] = round(normalized * weight * 100, 2)
        score = round(sum(component_scores.values()), 2)
        coverage = 1 - (
            sum(normalized_weights[signal] for signal in missing_signals)
        )
        confidence = "medium" if coverage >= 0.9 else "low" if coverage >= 0.6 else "insufficient"
        thresholds = self.config.recommendation_thresholds
        recommendation = (
            "advance_to_testing"
            if score >= thresholds.advance_to_testing
            else "review"
            if score >= thresholds.review
            else "defer"
        )
        location = location_candidate_from_geography_record(
            record,
            match_reason="public_data_prefilter",
            matched_alias=record.city,
        )
        leading = sorted(
            component_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:2]
        leading_text = ", ".join(
            f"{name.replace('_', ' ')} {points:.1f} points"
            for name, points in leading
        )
        return MarketPrefilterAssessment(
            assessment_version=self.config.version,
            config_hash=self.config_hash,
            geography_dataset_version=record.dataset_version,
            service_profile=profile_name,
            location=location,
            score=score,
            recommendation=recommendation,
            confidence=confidence,
            component_scores=component_scores,
            input_signals={
                **signals,
                "source": "acs_2024_5_year",
                "source_geoid": record.source_geoid,
                "land_area_sq_km": record.land_area_sq_km,
                "coverage": round(coverage, 4),
                "signal_weights": normalized_weights,
                "strong_values": profile.strong_values,
            },
            missing_signals=missing_signals,
            explanation=(
                f"{location.label} scored {score:.1f}/100 for the {profile_name.replace('_', ' ')} "
                f"public-data profile. Largest contributions: {leading_text}. This zero-cost "
                "prefilter measures market structure, not Google demand or ranking difficulty."
            ),
        )

    def profile_for_service(
        self,
        service: ServiceFamily,
    ) -> tuple[str, MarketPrefilterProfile]:
        service_text = " ".join(
            [
                service.id,
                service.display_name,
                service.description,
                *service.seed_queries,
                *service.provider_categories,
            ]
        ).lower()
        profiles = self.config.profiles
        for name, profile in profiles.items():
            terms = [term.lower() for term in profile.match_terms]
            if terms and any(term in service_text for term in terms):
                return name, profile
        fallback = profiles["generic_local_service"]
        return "generic_local_service", fallback


def _signals(record: USGeographyRecord) -> dict[str, float | int | None]:
    homeownership_rate = (
        record.owner_occupied_units / record.households
        if record.owner_occupied_units is not None
        and record.households is not None
        and record.households > 0
        else None
    )
    housing_age = (
        max(0, record.public_data_year - record.median_year_built)
        if record.median_year_built is not None
        else None
    )
    household_density = (
        record.households / record.land_area_sq_km
        if record.households is not None and record.land_area_sq_km > 0
        else None
    )
    return {
        "population": record.population,
        "households": record.households,
        "housing_units": record.housing_units,
        "owner_occupied_units": record.owner_occupied_units,
        "homeownership_rate": (
            round(homeownership_rate, 4)
            if homeownership_rate is not None
            else None
        ),
        "housing_age": housing_age,
        "median_year_built": record.median_year_built,
        "household_density": (
            round(household_density, 2)
            if household_density is not None
            else None
        ),
    }


def _normalize_signal(signal: str, value: float, strong_value: float) -> float:
    if strong_value <= 0:
        return 0.0
    if signal in {
        "population",
        "households",
        "housing_units",
        "owner_occupied_units",
        "household_density",
    }:
        return min(1.0, math.log1p(max(0.0, value)) / math.log1p(strong_value))
    return min(1.0, max(0.0, value) / strong_value)
