from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from rank_rent.domain.models import LocationType, Market, slugify
from rank_rent.services.us_geography import (
    STATE_NAMES,
    USGeographyError,
    USGeographyIndex,
    USGeographyMatch,
    USGeographyRecord,
)
from rank_rent.settings import Settings

COUNTRY_ALIASES = {
    "us": "US",
    "usa": "US",
    "u s": "US",
    "united states": "US",
    "united states of america": "US",
}


class LocationCandidate(BaseModel):
    id: str
    label: str
    type: LocationType
    country: str = "US"
    state: str
    city: str
    postal_code: str | None = None
    county: str
    county_fips: str
    metro: str
    metro_code: str | None = None
    latitude: float
    longitude: float
    population: int
    reference_population: int
    aliases: list[str] = Field(default_factory=list)
    postal_codes: list[str] = Field(default_factory=list)
    boundary_radius_km: float
    geography_id: str
    geography_dataset_version: str
    provider_location_code: str | None = None
    provider_location_name: str | None = None
    source: str = "offline_us_geography"
    confidence: float = Field(ge=0, le=1)
    match_reason: str
    resolution_metadata: dict[str, Any] = Field(default_factory=dict)

    def to_market(self) -> Market:
        provider_name = self.provider_location_name or _infer_provider_location_name(
            self.city,
            self.state,
        )
        return Market(
            id=slugify(self.geography_id),
            slug=slugify(self.geography_id),
            display_name=self.label,
            type=self.type,
            country_code=self.country,
            state=self.state,
            cities=[self.city],
            postal_codes=self.postal_codes,
            county=self.county,
            county_fips=self.county_fips,
            metro=self.metro,
            metro_code=self.metro_code,
            latitude=self.latitude,
            longitude=self.longitude,
            population=self.population,
            reference_population=self.reference_population,
            aliases=self.aliases,
            boundary_radius_km=self.boundary_radius_km,
            geography_id=self.geography_id,
            geography_dataset_version=self.geography_dataset_version,
            provider_location_code=self.provider_location_code,
            provider_location_name=provider_name,
            resolution_metadata={
                **self.resolution_metadata,
                "selected_location_id": self.geography_id,
                "selected_location_source": self.source,
                "selected_location_confidence": self.confidence,
                "selected_location_match_reason": self.match_reason,
                "dataforseo_mapping_status": (
                    "provider_code" if self.provider_location_code else "inferred_provider_name"
                ),
            },
        )


class LocationResolutionError(ValueError):
    def __init__(self, message: str, candidates: list[LocationCandidate] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


async def search_locations(
    session: Session,
    query: str,
    country: str,
    settings: Settings,
    limit: int = 8,
) -> list[LocationCandidate]:
    del session
    if normalize_country(country) != "US" or len(query.strip()) < 2:
        return []
    index = USGeographyIndex.from_settings(settings)
    return [_candidate_from_match(match) for match in index.search(query, limit=limit)]


async def resolve_market_for_scan(
    session: Session,
    location_text: str,
    country: str,
    settings: Settings,
    selected_location: LocationCandidate | None = None,
) -> Market:
    normalized_country = normalize_country(country)
    if normalized_country != "US":
        raise LocationResolutionError(
            "Discovery currently supports U.S. cities and ZIP codes only."
        )

    try:
        index = USGeographyIndex.from_settings(settings)
    except USGeographyError as exc:
        raise LocationResolutionError(str(exc)) from exc
    if selected_location is not None:
        record = index.get(selected_location.geography_id)
        if record is None:
            raise LocationResolutionError(
                "The selected location is no longer in the offline geography index. "
                "Search for it again."
            )
        return _candidate_from_record(
            record,
            confidence=1.0,
            match_reason="selected_canonical_location",
            matched_alias=selected_location.label,
        ).to_market()

    candidates = await search_locations(
        session,
        location_text,
        normalized_country,
        settings,
        limit=12,
    )
    if not candidates:
        raise LocationResolutionError(
            "Could not resolve that U.S. city or ZIP code. "
            "Enter a city with its state or choose a dropdown result."
        )

    exact = [
        candidate
        for candidate in candidates
        if candidate.match_reason in {"exact_zip", "exact_city_state", "exact_city"}
    ]
    if len(exact) == 1:
        return exact[0].to_market()

    raise LocationResolutionError(
        "That location is ambiguous. Select one of the suggested locations before scanning.",
        candidates=candidates,
    )


def normalize_country(value: str) -> str:
    cleaned = " ".join(value.lower().replace(".", " ").split())
    return COUNTRY_ALIASES.get(cleaned, value.strip().upper()[:2] or "US")


def market_from_geography_record(record: USGeographyRecord) -> Market:
    return _candidate_from_record(
        record,
        confidence=1.0,
        match_reason="canonical_geography_record",
        matched_alias=record.city,
    ).to_market()


def _candidate_from_match(match: USGeographyMatch) -> LocationCandidate:
    return _candidate_from_record(
        match.record,
        confidence=match.confidence,
        match_reason=match.match_reason,
        matched_alias=match.matched_alias,
    )


def _candidate_from_record(
    record: USGeographyRecord,
    *,
    confidence: float,
    match_reason: str,
    matched_alias: str,
) -> LocationCandidate:
    is_zip = record.kind == LocationType.postal_code.value
    label = (
        f"ZIP {record.postal_code} - {record.city}, {record.state}, US"
        if is_zip
        else f"{record.city}, {record.state}, US"
    )
    return LocationCandidate(
        id=record.id,
        label=label,
        type=LocationType.postal_code if is_zip else LocationType.city,
        state=record.state,
        city=record.city,
        postal_code=record.postal_code,
        county=record.county,
        county_fips=record.county_fips,
        metro=record.metro,
        metro_code=record.metro_code,
        latitude=record.latitude,
        longitude=record.longitude,
        population=record.population,
        reference_population=record.reference_population,
        aliases=record.aliases,
        postal_codes=(
            [record.postal_code]
            if is_zip and record.postal_code
            else record.postal_codes
        ),
        boundary_radius_km=record.boundary_radius_km,
        geography_id=record.id,
        geography_dataset_version=record.dataset_version,
        provider_location_name=_infer_provider_location_name(record.city, record.state),
        confidence=confidence,
        match_reason=match_reason,
        resolution_metadata={
            "geography_source_geoid": record.source_geoid,
            "geography_dataset_version": record.dataset_version,
            "matched_alias": matched_alias,
            "county": record.county,
            "county_fips": record.county_fips,
            "metro": record.metro,
            "metro_code": record.metro_code,
            "population": record.population,
            "reference_population": record.reference_population,
            "boundary_radius_km": record.boundary_radius_km,
            "boundary_method": "area_equivalent_radius",
            "land_area_sq_km": record.land_area_sq_km,
        },
    )


def _infer_provider_location_name(city: str, state: str) -> str:
    state_name = STATE_NAMES.get(state.upper(), state)
    return f"{city},{state_name},United States"
