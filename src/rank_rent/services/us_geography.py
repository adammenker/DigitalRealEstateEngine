from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from rank_rent.settings import Settings

STATE_ABBREVIATIONS = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
STATE_NAMES = {abbreviation: name.title() for name, abbreviation in STATE_ABBREVIATIONS.items()}


class USGeographyError(ValueError):
    pass


@dataclass(frozen=True)
class USGeographyRecord:
    id: str
    kind: str
    city: str
    state: str
    postal_code: str | None
    county: str
    county_fips: str
    metro: str
    metro_code: str | None
    metro_type: str
    latitude: float
    longitude: float
    population: int
    reference_population: int
    aliases: list[str]
    postal_codes: list[str]
    boundary_radius_km: float
    land_area_sq_km: float
    source_geoid: str
    dataset_version: str


@dataclass(frozen=True)
class USGeographyMatch:
    record: USGeographyRecord
    confidence: float
    match_reason: str
    matched_alias: str


class USGeographyIndex:
    def __init__(self, path: Path) -> None:
        self.path = path
        if not path.is_file():
            raise USGeographyError(
                f"Offline U.S. geography database is missing at {path}. "
                "Run scripts/build_us_geography.py."
            )

    @classmethod
    def from_settings(cls, settings: Settings) -> USGeographyIndex:
        path = settings.us_geography_database_path
        resolved = path if path.is_absolute() else settings.project_root / path
        return cls(resolved)

    def get(self, geography_id: str) -> USGeographyRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM geographies WHERE id = ?",
                (geography_id,),
            ).fetchone()
        return _record(row) if row else None

    def search(self, query: str, limit: int = 8) -> list[USGeographyMatch]:
        cleaned = query.strip()
        if not cleaned:
            return []
        zip_match = re.fullmatch(r"(\d{5})(?:-\d{4})?", cleaned)
        if zip_match:
            return self._search_zip(zip_match.group(1))

        city_query, state = _parse_city_state(cleaned)
        normalized = normalize_location(city_query)
        if len(normalized) < 2:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT g.*, a.alias_norm, a.alias, a.priority
                FROM geography_aliases AS a
                JOIN geographies AS g ON g.id = a.geography_id
                WHERE g.kind = 'city'
                  AND (? IS NULL OR g.state = ?)
                  AND (
                    a.alias_norm = ?
                    OR a.alias_norm LIKE ?
                    OR a.alias_norm LIKE ?
                  )
                ORDER BY
                    CASE WHEN a.alias_norm = ? THEN 0
                         WHEN a.alias_norm LIKE ? THEN 1
                         ELSE 2 END,
                    a.priority DESC,
                    g.population DESC
                LIMIT 400
                """,
                (
                    state,
                    state,
                    normalized,
                    f"{normalized}%",
                    f"{normalized[:2]}%",
                    normalized,
                    f"{normalized}%",
                ),
            ).fetchall()
        best: dict[str, USGeographyMatch] = {}
        for row in rows:
            alias_norm = str(row["alias_norm"])
            score, reason = _match_score(normalized, alias_norm, state is not None)
            if score < 0.55:
                continue
            record = _record(row)
            match = USGeographyMatch(
                record=record,
                confidence=score,
                match_reason=reason,
                matched_alias=str(row["alias"]),
            )
            existing = best.get(record.id)
            if existing is None or match.confidence > existing.confidence:
                best[record.id] = match
        return sorted(
            best.values(),
            key=lambda match: (
                match.confidence,
                match.record.population,
                match.record.city,
            ),
            reverse=True,
        )[:limit]

    def metadata(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT key, value FROM metadata").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def _search_zip(self, postal_code: str) -> list[USGeographyMatch]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM geographies WHERE postal_code = ?",
                (postal_code,),
            ).fetchone()
        if row is None:
            return []
        return [
            USGeographyMatch(
                record=_record(row),
                confidence=1.0,
                match_reason="exact_zip",
                matched_alias=postal_code,
            )
        ]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection


def validate_market_against_index(
    market: Any,
    settings: Settings,
) -> USGeographyRecord:
    if str(market.country_code).upper() != "US":
        raise USGeographyError("Production discovery currently supports U.S. markets only.")
    geography_id = str(market.geography_id or "")
    if not geography_id:
        raise USGeographyError(
            "The market is not linked to the offline U.S. geography index. "
            "Select a city or ZIP from the location dropdown."
        )
    record = USGeographyIndex.from_settings(settings).get(geography_id)
    if record is None:
        raise USGeographyError(
            "The selected geography is no longer present in the offline index. "
            "Search for the location again."
        )
    expected_values = {
        "type": record.kind,
        "state": record.state,
        "cities": [record.city],
        "postal_codes": (
            [record.postal_code]
            if record.kind == "postal_code" and record.postal_code
            else record.postal_codes
        ),
        "county": record.county,
        "county_fips": record.county_fips,
        "metro": record.metro,
        "metro_code": record.metro_code,
        "aliases": record.aliases,
        "geography_dataset_version": record.dataset_version,
    }
    for field_name, expected_value in expected_values.items():
        actual_value: Any = getattr(market, field_name, None)
        if field_name == "type" and actual_value is not None and hasattr(actual_value, "value"):
            actual_value = actual_value.value
        if actual_value != expected_value:
            raise USGeographyError(
                f"The selected market has stale or invalid {field_name}. "
                "Search for the location again before planning."
            )

    expected_numeric_values: dict[str, float | int] = {
        "latitude": record.latitude,
        "longitude": record.longitude,
        "population": record.population,
        "reference_population": record.reference_population,
        "boundary_radius_km": record.boundary_radius_km,
    }
    for numeric_field, numeric_expected in expected_numeric_values.items():
        actual_numeric: Any = getattr(market, numeric_field, None)
        if (
            actual_numeric is None
            or abs(float(actual_numeric) - float(numeric_expected)) > 0.0001
        ):
            raise USGeographyError(
                f"The selected market has stale or invalid {numeric_field}. "
                "Search for the location again before planning."
            )
    if not market.provider_location_code:
        expected_provider_name = (
            f"{record.city},{STATE_NAMES[record.state]},United States"
        )
        if market.provider_location_name != expected_provider_name:
            raise USGeographyError(
                "The selected market has a stale or invalid provider_location_name. "
                "Search for the location again before planning."
            )
    return record


def normalize_location(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _parse_city_state(value: str) -> tuple[str, str | None]:
    normalized = normalize_location(value)
    tokens = normalized.split()
    while tokens and tokens[-1] in {"us", "usa", "united", "states", "america"}:
        tokens.pop()
    if not tokens:
        return "", None
    last = tokens[-1].upper()
    if last in STATE_NAMES:
        return " ".join(tokens[:-1]), last
    for state_name, abbreviation in sorted(
        STATE_ABBREVIATIONS.items(),
        key=lambda item: len(item[0].split()),
        reverse=True,
    ):
        state_tokens = state_name.split()
        if tokens[-len(state_tokens) :] == state_tokens:
            return " ".join(tokens[: -len(state_tokens)]), abbreviation
    return " ".join(tokens), None


def _match_score(query: str, alias: str, state_supplied: bool) -> tuple[float, str]:
    state_bonus = 0.02 if state_supplied else 0
    if query == alias:
        return min(1.0, 0.97 + state_bonus), (
            "exact_city_state" if state_supplied else "exact_city"
        )
    if alias.startswith(query):
        return min(0.95, 0.88 + state_bonus), "city_prefix"
    ratio = SequenceMatcher(None, query, alias).ratio()
    query_tokens = set(query.split())
    alias_tokens = set(alias.split())
    overlap = len(query_tokens & alias_tokens) / max(1, len(query_tokens))
    return min(0.89, max(ratio, overlap * 0.85) + state_bonus), "fuzzy_city"


def _record(row: sqlite3.Row) -> USGeographyRecord:
    return USGeographyRecord(
        id=str(row["id"]),
        kind=str(row["kind"]),
        city=str(row["city"]),
        state=str(row["state"]),
        postal_code=str(row["postal_code"]) if row["postal_code"] else None,
        county=str(row["county"]),
        county_fips=str(row["county_fips"]),
        metro=str(row["metro"]),
        metro_code=str(row["metro_code"]) if row["metro_code"] else None,
        metro_type=str(row["metro_type"]),
        latitude=float(row["latitude"]),
        longitude=float(row["longitude"]),
        population=int(row["population"]),
        reference_population=int(row["reference_population"]),
        aliases=list(json.loads(row["aliases_json"])),
        postal_codes=list(json.loads(row["postal_codes_json"])),
        boundary_radius_km=float(row["boundary_radius_km"]),
        land_area_sq_km=float(row["land_area_sq_km"]),
        source_geoid=str(row["source_geoid"]),
        dataset_version=str(row["dataset_version"]),
    )
