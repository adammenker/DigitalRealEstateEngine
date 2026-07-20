from __future__ import annotations

import hashlib
import math
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.public_data.catalog import load_dataset_catalog
from rank_rent.public_data.models import (
    DatasetKind,
    DatasetManifest,
    DatasetRecord,
    PublicDataSnapshot,
)
from rank_rent.public_data.naics import (
    MappingConfidence,
    NAICSMapping,
    NAICSRegistry,
    NAICSRelationship,
    load_naics_registry,
)
from rank_rent.public_data.store import PublicDataStore
from rank_rent.services.locations import (
    LocationCandidate,
    location_candidate_from_geography_record,
)
from rank_rent.services.us_geography import USGeographyIndex, USGeographyRecord
from rank_rent.settings import Settings


class SignalDirection(StrEnum):
    higher = "higher"
    ideal_range = "ideal_range"


class SignalTransform(StrEnum):
    linear = "linear"
    log = "log"


class IdealRange(BaseModel):
    minimum: float = Field(ge=0)
    maximum: float = Field(ge=0)
    hard_minimum: float = Field(ge=0)
    hard_maximum: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> IdealRange:
        if not (
            self.hard_minimum <= self.minimum <= self.maximum <= self.hard_maximum
        ):
            raise ValueError(
                "Ideal-range bounds must satisfy hard_minimum <= minimum <= "
                "maximum <= hard_maximum."
            )
        return self


class AddressableMarketSignalProfile(BaseModel):
    weight: float = Field(gt=0, le=1)
    source_dataset: DatasetKind
    source_measure: str
    causal_rationale: str
    expected_direction: SignalDirection
    missing_data_treatment: str
    geographic_granularity: str
    refresh_cadence: str
    transform: SignalTransform = SignalTransform.linear
    strong_value: float | None = Field(default=None, gt=0)
    ideal_range: IdealRange | None = None
    maximum_credit: float = Field(default=1, gt=0, le=1)

    @model_validator(mode="after")
    def validate_normalization(self) -> AddressableMarketSignalProfile:
        if self.expected_direction == SignalDirection.higher:
            if self.strong_value is None or self.ideal_range is not None:
                raise ValueError(
                    "Higher-is-better signals require strong_value and no ideal_range."
                )
        elif self.ideal_range is None or self.strong_value is not None:
            raise ValueError(
                "Ideal-range signals require ideal_range and no strong_value."
            )
        return self

    @field_validator(
        "source_measure",
        "causal_rationale",
        "missing_data_treatment",
        "geographic_granularity",
        "refresh_cadence",
    )
    @classmethod
    def require_documentation(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Every addressable-market signal must be documented.")
        return cleaned


class AddressableMarketProfile(BaseModel):
    service_family_id: str
    profile_version: int = Field(ge=1)
    legacy_profile_name: str = "home_services"
    target_household_signal: Literal["households", "owner_occupied_units"] = (
        "owner_occupied_units"
    )
    minimum_evidence_coverage: float = Field(default=0.5, ge=0, le=1)
    signals: dict[str, AddressableMarketSignalProfile]

    @model_validator(mode="after")
    def validate_weights(self) -> AddressableMarketProfile:
        total = sum(signal.weight for signal in self.signals.values())
        if not math.isclose(total, 1.0, abs_tol=0.0001):
            raise ValueError(
                f"Signal weights for {self.service_family_id} must sum to 1.0, "
                f"not {total:.4f}."
            )
        return self


class RecommendationThresholds(BaseModel):
    advance_to_testing: float = Field(ge=0, le=100)
    review: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_order(self) -> RecommendationThresholds:
        if self.review >= self.advance_to_testing:
            raise ValueError("review must be lower than advance_to_testing.")
        return self


class AddressableMarketConfig(BaseModel):
    assessment_version: str
    minimum_population: int = Field(ge=1)
    maximum_results: int = Field(ge=1, le=10_000)
    recommendation_thresholds: RecommendationThresholds
    signal_templates: dict[str, AddressableMarketSignalProfile] = Field(
        default_factory=dict
    )
    profiles: dict[str, AddressableMarketProfile]

    @model_validator(mode="after")
    def validate_profiles(self) -> AddressableMarketConfig:
        fallback = self.profiles.get("generic_local_service")
        if fallback is None:
            raise ValueError("generic_local_service is required as the fallback profile.")
        for profile_id, profile in self.profiles.items():
            if profile_id != profile.service_family_id:
                raise ValueError(
                    f"Profile key {profile_id} must match service_family_id "
                    f"{profile.service_family_id}."
                )
        return self

    @property
    def version(self) -> str:
        """Compatibility property used by the existing API persistence layer."""

        return self.assessment_version


class SignalEvidence(BaseModel):
    signal: str
    raw_value: float | int | None
    normalized_value: float | None
    configured_weight: float
    points: float
    available: bool
    source_dataset: str
    source_measure: str
    source_version: str | None
    data_year: int | None
    release_date: date | None
    geographic_granularity: str
    causal_rationale: str
    expected_direction: SignalDirection
    missing_data_treatment: str
    refresh_cadence: str
    limitations: list[str] = Field(default_factory=list)


class ProviderDensityEvidence(BaseModel):
    target_households: int | None
    target_household_signal: str
    employer_establishments_raw: float | None
    nonemployer_businesses_raw: float | None
    employer_establishments_weighted: float | None
    nonemployer_businesses_weighted: float | None
    employer_establishments_per_10000: float | None
    nonemployer_businesses_per_10000: float | None
    combined_supply_density: float | None
    combined_supply_band: str
    data_confidence: str
    naics_registry_version: str
    naics_mappings: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class AddressableMarketAssessment(BaseModel):
    assessment_type: Literal["addressable_market"] = "addressable_market"
    assessment_version: str
    config_hash: str
    geography_dataset_version: str
    service_family_id: str
    service_profile: str
    profile_version: int
    location: LocationCandidate
    score: float | None = Field(default=None, ge=0, le=100)
    score_available: bool
    rank: int | None = None
    recommendation: str
    confidence: str
    evidence_coverage: float = Field(ge=0, le=1)
    component_scores: dict[str, float] = Field(default_factory=dict)
    evidence: list[SignalEvidence] = Field(default_factory=list)
    provider_density: ProviderDensityEvidence
    input_signals: dict[str, Any] = Field(default_factory=dict)
    missing_signals: list[str] = Field(default_factory=list)
    dataset_versions: dict[str, str] = Field(default_factory=dict)
    data_age_warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    explanation: str


class AddressableMarketBatch(BaseModel):
    assessment_version: str
    service_family_id: str
    candidate_count: int
    scored_count: int
    returned_count: int
    zero_cost: Literal[True] = True
    paid_api_calls: Literal[0] = 0
    assessments: list[AddressableMarketAssessment]


class AddressableMarketPrefilter:
    """Ranks market plausibility using only local public-data evidence."""

    def __init__(
        self,
        index: USGeographyIndex,
        config_path: Path = Path("config/market_prefilter.yaml"),
        *,
        public_data_store: PublicDataStore | None = None,
        dataset_catalog_path: Path | None = None,
        naics_registry_path: Path | None = None,
    ) -> None:
        resolved_config_path = _resolve_config_path(config_path)
        raw_config = resolved_config_path.read_text()
        self.config = AddressableMarketConfig.model_validate(yaml.safe_load(raw_config))
        config_root = resolved_config_path.parents[1]
        self.dataset_catalog = load_dataset_catalog(
            dataset_catalog_path or config_root / "public_data" / "datasets.yaml"
        )
        self.naics_registry = load_naics_registry(
            naics_registry_path or config_root / "addressable_market" / "naics.yaml"
        )
        hash_input = "\n".join(
            [
                raw_config,
                (dataset_catalog_path or config_root / "public_data" / "datasets.yaml").read_text(),
                (naics_registry_path or config_root / "addressable_market" / "naics.yaml").read_text(),
            ]
        )
        self.config_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
        self.index = index
        self.public_data_store = public_data_store or PublicDataStore(
            config_root.parent / "data" / "public_data"
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> AddressableMarketPrefilter:
        return cls(
            USGeographyIndex.from_settings(settings),
            settings.project_root / "config" / "market_prefilter.yaml",
            public_data_store=PublicDataStore(
                settings.project_root / "data" / "public_data"
            ),
        )

    def rank_markets(
        self,
        service: ServiceFamily,
        *,
        states: list[str] | None = None,
        geography_kind: str = "city",
        limit: int = 20,
        minimum_population: int | None = None,
    ) -> tuple[list[AddressableMarketAssessment], int]:
        records = self.index.list_markets(
            kind=geography_kind,
            states=states or [],
            minimum_population=max(
                1,
                int(minimum_population)
                if minimum_population is not None
                else self.config.minimum_population,
            ),
        )
        batch = self.assess_batch(service, records, limit=limit)
        return batch.assessments, batch.candidate_count

    def assess_batch(
        self,
        service: ServiceFamily,
        records: list[USGeographyRecord],
        *,
        limit: int | None = None,
    ) -> AddressableMarketBatch:
        bounded_limit = max(
            1,
            min(
                limit if limit is not None else self.config.maximum_results,
                self.config.maximum_results,
            ),
        )
        assessments = [self.assess_record(service, record) for record in records]
        assessments.sort(key=_assessment_sort_key, reverse=True)
        selected = assessments[:bounded_limit]
        for rank, assessment in enumerate(selected, start=1):
            assessment.rank = rank
        return AddressableMarketBatch(
            assessment_version=self.config.assessment_version,
            service_family_id=service.id,
            candidate_count=len(records),
            scored_count=sum(item.score_available for item in assessments),
            returned_count=len(selected),
            assessments=selected,
        )

    def assess_geography_ids(
        self,
        service: ServiceFamily,
        geography_ids: list[str],
        *,
        limit: int | None = None,
    ) -> AddressableMarketBatch:
        records = [
            record
            for geography_id in geography_ids
            if (record := self.index.get(geography_id)) is not None
        ]
        return self.assess_batch(service, records, limit=limit)

    def assess_market(
        self,
        service: ServiceFamily,
        market: Market,
    ) -> AddressableMarketAssessment | None:
        if not market.geography_id:
            return None
        record = self.index.get(market.geography_id)
        return self.assess_record(service, record) if record is not None else None

    def assess_record(
        self,
        service: ServiceFamily,
        record: USGeographyRecord,
    ) -> AddressableMarketAssessment:
        profile_id, profile = self._profile_for_service(service)
        snapshot = self.public_data_store.snapshot(
            record.id,
            county_fips=record.county_fips,
            warning_age_days=self.dataset_catalog.warning_age_days,
        )
        signals, provenance = _market_signals(record, snapshot)
        provider_density = _provider_density(
            snapshot=snapshot,
            profile=profile,
            signals=signals,
            service_mapping=self.naics_registry.for_service(profile_id),
            registry=self.naics_registry,
        )
        signals.update(
            {
                "employer_establishments_per_10000": (
                    provider_density.employer_establishments_per_10000
                ),
                "nonemployer_businesses_per_10000": (
                    provider_density.nonemployer_businesses_per_10000
                ),
                "combined_supply_density": provider_density.combined_supply_density,
            }
        )
        supply_provenance = _supply_provenance(snapshot, provider_density)
        for signal_name in (
            "employer_establishments_per_10000",
            "nonemployer_businesses_per_10000",
            "combined_supply_density",
        ):
            provenance[signal_name] = supply_provenance
        evidence: list[SignalEvidence] = []
        component_scores: dict[str, float] = {}
        missing_signals: list[str] = []
        available_weight = 0.0
        for signal_name, signal_profile in profile.signals.items():
            value = signals.get(signal_name)
            source = provenance.get(signal_name)
            if value is None:
                missing_signals.append(signal_name)
                component_scores[signal_name] = 0.0
                evidence.append(
                    _signal_evidence(
                        signal_name,
                        signal_profile,
                        value=None,
                        normalized=None,
                        points=0,
                        source=source,
                    )
                )
                continue
            normalized = _normalize_signal(float(value), signal_profile)
            points = round(
                normalized
                * signal_profile.maximum_credit
                * signal_profile.weight
                * 100,
                2,
            )
            available_weight += signal_profile.weight
            component_scores[signal_name] = points
            evidence.append(
                _signal_evidence(
                    signal_name,
                    signal_profile,
                    value=value,
                    normalized=normalized,
                    points=points,
                    source=source,
                )
            )
        coverage = round(available_weight, 4)
        score_available = coverage >= profile.minimum_evidence_coverage
        score = round(sum(component_scores.values()), 2) if score_available else None
        confidence = _assessment_confidence(
            coverage=coverage,
            warnings=snapshot.warnings,
            provider_confidence=provider_density.data_confidence,
        )
        thresholds = self.config.recommendation_thresholds
        recommendation = (
            "insufficient_evidence"
            if score is None
            else "advance_to_testing"
            if score >= thresholds.advance_to_testing
            else "review"
            if score >= thresholds.review
            else "defer"
        )
        location = location_candidate_from_geography_record(
            record,
            match_reason="addressable_market_public_data",
            matched_alias=record.city,
        )
        dataset_versions = {
            dataset.value: manifest.release.version
            for dataset, manifest in snapshot.manifests.items()
        }
        dataset_versions.setdefault("acs", record.dataset_version)
        limitations = [
            "This assessment measures addressable-market structure, not SEO difficulty, "
            "Google demand, ranking feasibility, or expected revenue.",
            "Industry establishment counts are NAICS-based supply estimates, not exact "
            "service-provider or tenant counts.",
        ]
        if provider_density.combined_supply_density is None:
            limitations.append(
                "No activated CBP/NES supply evidence was available; provider-density "
                "signals received no points."
            )
        leading = sorted(
            component_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        leading_text = ", ".join(
            f"{name.replace('_', ' ')} {points:.1f} points"
            for name, points in leading
        )
        display_score = f"{score:.1f}/100" if score is not None else "unscored"
        return AddressableMarketAssessment(
            assessment_version=self.config.assessment_version,
            config_hash=self.config_hash,
            geography_dataset_version=record.dataset_version,
            service_family_id=profile_id,
            service_profile=profile.legacy_profile_name,
            profile_version=profile.profile_version,
            location=location,
            score=score,
            score_available=score_available,
            recommendation=recommendation,
            confidence=confidence,
            evidence_coverage=coverage,
            component_scores=component_scores,
            evidence=evidence,
            provider_density=provider_density,
            input_signals={
                **signals,
                "source": "activated_public_data_with_embedded_acs_fallback",
                "source_geoid": record.source_geoid,
                "county_fips": record.county_fips,
                "land_area_sq_km": record.land_area_sq_km,
                "coverage": coverage,
                "signal_weights": {
                    name: item.weight for name, item in profile.signals.items()
                },
            },
            missing_signals=missing_signals,
            dataset_versions=dataset_versions,
            data_age_warnings=snapshot.warnings,
            limitations=limitations,
            explanation=(
                f"{location.label} is {display_score} under "
                f"{profile_id} profile v{profile.profile_version}. "
                f"Largest contributions: {leading_text or 'none'}. "
                "This zero-cost result is kept separate from SEO-opportunity scoring."
            ),
        )

    def profile_for_service(
        self,
        service: ServiceFamily,
    ) -> tuple[str, AddressableMarketProfile]:
        """Compatibility surface returning the existing display profile label."""

        _, profile = self._profile_for_service(service)
        return profile.legacy_profile_name, profile

    def _profile_for_service(
        self,
        service: ServiceFamily,
    ) -> tuple[str, AddressableMarketProfile]:
        profile = self.config.profiles.get(service.id)
        if profile is not None:
            return service.id, profile
        fallback = self.config.profiles["generic_local_service"]
        return "generic_local_service", fallback


def _resolve_config_path(path: Path) -> Path:
    payload = yaml.safe_load(path.read_text())
    if isinstance(payload, dict) and "addressable_market_config" in payload:
        target = Path(str(payload["addressable_market_config"]))
        return target if target.is_absolute() else path.parent / target
    return path


def _market_signals(
    record: USGeographyRecord,
    snapshot: PublicDataSnapshot,
) -> tuple[dict[str, float | int | None], dict[str, dict[str, Any]]]:
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
    signals: dict[str, float | int | None] = {
        "population": record.population,
        "households": record.households,
        "housing_units": record.housing_units,
        "owner_occupied_units": record.owner_occupied_units,
        "homeownership_rate": homeownership_rate,
        "housing_age": housing_age,
        "median_year_built": record.median_year_built,
        "household_density": household_density,
        "detached_housing_share": None,
        "median_household_income": None,
        "purchasing_power": None,
        "storm_exposure": None,
        "hazard_exposure": None,
    }
    embedded_source = {
        "source_version": record.dataset_version,
        "data_year": record.public_data_year,
        "release_date": None,
        "limitations": [
            "Embedded ACS baseline; activate a separately ingested ACS release for "
            "full release provenance."
        ],
    }
    provenance = {
        signal: embedded_source
        for signal in (
            "population",
            "households",
            "housing_units",
            "owner_occupied_units",
            "homeownership_rate",
            "housing_age",
            "median_year_built",
            "household_density",
        )
    }
    for dataset in (DatasetKind.acs, DatasetKind.noaa, DatasetKind.fema):
        records = snapshot.records.get(dataset, [])
        manifest = snapshot.manifests.get(dataset)
        for public_record in records:
            for measure, value in public_record.values.items():
                signals[measure] = value
                provenance[measure] = _manifest_provenance(manifest)
    if signals.get("homeownership_rate") is None:
        households = signals.get("households")
        occupied = signals.get("owner_occupied_units")
        if households and occupied is not None:
            signals["homeownership_rate"] = float(occupied) / float(households)
    median_year_built = signals.get("median_year_built")
    if signals.get("housing_age") is None and isinstance(
        median_year_built,
        (int, float),
    ):
        signals["housing_age"] = max(
            0,
            record.public_data_year - int(median_year_built),
        )
    household_count = signals.get("households")
    if signals.get("household_density") is None and isinstance(
        household_count,
        (int, float),
    ):
        signals["household_density"] = (
            float(household_count) / record.land_area_sq_km
            if record.land_area_sq_km > 0
            else None
        )
    if signals.get("purchasing_power") is None:
        signals["purchasing_power"] = signals.get("median_household_income")
        if "median_household_income" in provenance:
            provenance["purchasing_power"] = provenance["median_household_income"]
    return signals, provenance


def _provider_density(
    *,
    snapshot: PublicDataSnapshot,
    profile: AddressableMarketProfile,
    signals: dict[str, float | int | None],
    service_mapping: Any,
    registry: NAICSRegistry,
) -> ProviderDensityEvidence:
    target_households_value = signals.get(profile.target_household_signal)
    target_households = (
        int(target_households_value)
        if isinstance(target_households_value, (int, float))
        and target_households_value > 0
        else None
    )
    mappings: list[NAICSMapping] = (
        service_mapping.mappings if service_mapping is not None else []
    )
    mapping_by_code = {mapping.code: mapping for mapping in mappings}
    cbp_raw, cbp_weighted, cbp_used = _aggregate_industry_records(
        snapshot.records.get(DatasetKind.cbp, []),
        mapping_by_code,
        "establishments",
    )
    nes_raw, nes_weighted, nes_used = _aggregate_industry_records(
        snapshot.records.get(DatasetKind.nes, []),
        mapping_by_code,
        "nonemployer_businesses",
    )
    employer_density = _density(cbp_weighted, target_households)
    nonemployer_density = _density(nes_weighted, target_households)
    available_densities = [
        value for value in (employer_density, nonemployer_density) if value is not None
    ]
    combined_density = (
        round(sum(available_densities), 3) if available_densities else None
    )
    supply_profile = profile.signals.get("combined_supply_density")
    supply_band = _supply_band(
        combined_density,
        supply_profile.ideal_range if supply_profile is not None else None,
    )
    used_mappings = cbp_used | nes_used
    confidences = {
        mapping_by_code[code].confidence
        for code in used_mappings
        if code in mapping_by_code
    }
    relationships = {
        mapping_by_code[code].relationship
        for code in used_mappings
        if code in mapping_by_code
    }
    data_confidence = (
        "insufficient"
        if combined_density is None
        else "high"
        if confidences == {MappingConfidence.high}
        and relationships == {NAICSRelationship.exact}
        and cbp_weighted is not None
        and nes_weighted is not None
        else "medium"
        if MappingConfidence.low not in confidences
        else "low"
    )
    limitations = [
        "Weighted counts apply explicit discounts to broad-parent and adjacent "
        "NAICS mappings; they are not exact provider counts."
    ]
    if cbp_weighted is None:
        limitations.append("No active CBP records matched the reviewed NAICS registry.")
    if nes_weighted is None:
        limitations.append("No active NES records matched the reviewed NAICS registry.")
    return ProviderDensityEvidence(
        target_households=target_households,
        target_household_signal=profile.target_household_signal,
        employer_establishments_raw=cbp_raw,
        nonemployer_businesses_raw=nes_raw,
        employer_establishments_weighted=cbp_weighted,
        nonemployer_businesses_weighted=nes_weighted,
        employer_establishments_per_10000=employer_density,
        nonemployer_businesses_per_10000=nonemployer_density,
        combined_supply_density=combined_density,
        combined_supply_band=supply_band,
        data_confidence=data_confidence,
        naics_registry_version=registry.registry_version,
        naics_mappings=[
            {
                **mapping.model_dump(mode="json"),
                "evidence_weight": mapping.evidence_weight,
                "used": mapping.code in used_mappings,
            }
            for mapping in mappings
        ],
        limitations=limitations,
    )


def _aggregate_industry_records(
    records: list[DatasetRecord],
    mappings: dict[str, NAICSMapping],
    measure: str,
) -> tuple[float | None, float | None, set[str]]:
    raw_total = 0.0
    weighted_total = 0.0
    matched = False
    used: set[str] = set()
    for record in records:
        code = record.dimensions.get("naics_code")
        mapping = mappings.get(code or "")
        value = record.values.get(measure)
        if mapping is None or value is None:
            continue
        matched = True
        used.add(mapping.code)
        raw_total += float(value)
        weighted_total += float(value) * mapping.evidence_weight
    if not matched:
        return None, None, used
    return round(raw_total, 3), round(weighted_total, 3), used


def _density(value: float | None, households: int | None) -> float | None:
    if value is None or households is None or households <= 0:
        return None
    return round(value * 10_000 / households, 3)


def _supply_band(value: float | None, ideal_range: IdealRange | None) -> str:
    if value is None or ideal_range is None:
        return "unknown"
    if ideal_range.minimum <= value <= ideal_range.maximum:
        return "ideal"
    if value < ideal_range.minimum:
        return "undersupplied"
    return "oversupplied"


def _signal_evidence(
    name: str,
    profile: AddressableMarketSignalProfile,
    *,
    value: float | int | None,
    normalized: float | None,
    points: float,
    source: dict[str, Any] | None,
) -> SignalEvidence:
    source_payload = source or {}
    return SignalEvidence(
        signal=name,
        raw_value=value,
        normalized_value=round(normalized, 4) if normalized is not None else None,
        configured_weight=profile.weight,
        points=points,
        available=value is not None,
        source_dataset=profile.source_dataset.value,
        source_measure=profile.source_measure,
        source_version=source_payload.get("source_version"),
        data_year=source_payload.get("data_year"),
        release_date=source_payload.get("release_date"),
        geographic_granularity=profile.geographic_granularity,
        causal_rationale=profile.causal_rationale,
        expected_direction=profile.expected_direction,
        missing_data_treatment=profile.missing_data_treatment,
        refresh_cadence=profile.refresh_cadence,
        limitations=list(source_payload.get("limitations", [])),
    )


def _manifest_provenance(manifest: DatasetManifest | None) -> dict[str, Any]:
    if manifest is None:
        return {}
    return {
        "source_version": manifest.release.version,
        "data_year": manifest.release.data_year,
        "release_date": manifest.release.release_date,
        "limitations": [],
    }


def _supply_provenance(
    snapshot: PublicDataSnapshot,
    density: ProviderDensityEvidence,
) -> dict[str, Any]:
    manifests = [
        snapshot.manifests[dataset]
        for dataset in (DatasetKind.cbp, DatasetKind.nes)
        if dataset in snapshot.manifests
    ]
    return {
        "source_version": (
            "+".join(
                f"{manifest.release.dataset.value}:{manifest.release.version}"
                for manifest in manifests
            )
            or None
        ),
        "data_year": min(
            (manifest.release.data_year for manifest in manifests),
            default=None,
        ),
        "release_date": max(
            (manifest.release.release_date for manifest in manifests),
            default=None,
        ),
        "limitations": density.limitations,
    }


def _normalize_signal(
    value: float,
    profile: AddressableMarketSignalProfile,
) -> float:
    safe_value = max(0.0, value)
    if profile.expected_direction == SignalDirection.ideal_range:
        bounds = profile.ideal_range
        if bounds is None:
            return 0.0
        if bounds.minimum <= safe_value <= bounds.maximum:
            return 1.0
        if safe_value < bounds.minimum:
            span = bounds.minimum - bounds.hard_minimum
            return (
                max(0.0, (safe_value - bounds.hard_minimum) / span)
                if span > 0
                else 0.0
            )
        span = bounds.hard_maximum - bounds.maximum
        return (
            max(0.0, (bounds.hard_maximum - safe_value) / span)
            if span > 0
            else 0.0
        )
    strong_value = profile.strong_value or 1.0
    if profile.transform == SignalTransform.log:
        return min(1.0, math.log1p(safe_value) / math.log1p(strong_value))
    return min(1.0, safe_value / strong_value)


def _assessment_confidence(
    *,
    coverage: float,
    warnings: list[str],
    provider_confidence: str,
) -> str:
    if coverage < 0.5:
        return "insufficient"
    if coverage >= 0.9 and not warnings and provider_confidence in {"high", "medium"}:
        return "high"
    if coverage >= 0.65 and not warnings:
        return "medium"
    return "low"


def _assessment_sort_key(
    assessment: AddressableMarketAssessment,
) -> tuple[float, float, int, str]:
    return (
        assessment.score if assessment.score is not None else -1,
        assessment.evidence_coverage,
        assessment.location.population,
        assessment.location.label,
    )


# Compatibility aliases keep existing API/scanner imports stable during the rename.
MarketPrefilterAssessment = AddressableMarketAssessment
MarketPrefilterProfile = AddressableMarketProfile
MarketPrefilterConfig = AddressableMarketConfig
MarketPrefilter = AddressableMarketPrefilter
