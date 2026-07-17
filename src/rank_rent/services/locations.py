from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import MarketORM, RawApiResponseORM
from rank_rent.domain.models import LocationType, Market, slugify
from rank_rent.services.seeds import SeedValidationError, load_markets
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
STATE_NAMES = {abbr: name.title() for name, abbr in STATE_ABBREVIATIONS.items()}
COUNTRY_ALIASES = {
    "us": "US",
    "usa": "US",
    "u s": "US",
    "united states": "US",
    "united states of america": "US",
    "gb": "GB",
    "uk": "GB",
    "united kingdom": "GB",
    "england": "GB",
    "ca": "CA",
    "canada": "CA",
}
COUNTRY_DISPLAY_NAMES = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
}


class LocationCandidate(BaseModel):
    id: str
    label: str
    type: LocationType = LocationType.city
    country: str = "US"
    state: str | None = None
    city: str | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    provider_location_code: str | None = None
    provider_location_name: str | None = None
    source: str
    confidence: float = Field(ge=0, le=1)
    match_reason: str
    resolution_metadata: dict[str, Any] = Field(default_factory=dict)

    def to_market(self) -> Market:
        cities = [self.city] if self.city else []
        postal_codes = [self.postal_code] if self.postal_code else []
        inferred_provider_name = self.provider_location_name or _infer_provider_location_name(
            city=self.city,
            state=self.state,
            country=self.country,
            location_type=self.type,
        )
        provider_mapping_status = (
            "provider_code"
            if self.provider_location_code
            else "provider_name"
            if self.provider_location_name
            else "inferred_provider_name"
            if inferred_provider_name
            else "unmapped"
        )
        return Market(
            id=self.id,
            slug=slugify(self.id),
            display_name=self.label,
            type=self.type,
            country_code=self.country,
            state=self.state,
            cities=cities,
            postal_codes=postal_codes,
            latitude=self.latitude,
            longitude=self.longitude,
            provider_location_code=self.provider_location_code,
            provider_location_name=inferred_provider_name,
            resolution_metadata={
                **self.resolution_metadata,
                "selected_location_id": self.id,
                "selected_location_source": self.source,
                "selected_location_confidence": self.confidence,
                "selected_location_match_reason": self.match_reason,
                "dataforseo_mapping_status": provider_mapping_status,
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
    cleaned = query.strip()
    normalized_country = normalize_country(country)
    if len(cleaned) < 2:
        return []

    candidates: list[LocationCandidate] = []
    candidates.extend(_explicit_candidates(cleaned, normalized_country))
    candidates.extend(_gazetteer_candidates(cleaned, normalized_country, settings))
    candidates.extend(_seed_candidates(cleaned, normalized_country, settings))
    candidates.extend(_db_market_candidates(session, cleaned, normalized_country))
    candidates.extend(_cached_dataforseo_candidates(session, cleaned, normalized_country))
    candidates.extend(await _pelias_candidates(cleaned, normalized_country, settings, limit))
    return _rank_and_dedupe(cleaned, candidates, limit)


async def resolve_market_for_scan(
    session: Session,
    location_text: str,
    country: str,
    settings: Settings,
    selected_location: LocationCandidate | None = None,
) -> Market:
    if selected_location is not None:
        return selected_location.to_market()

    candidates = await search_locations(session, location_text, country, settings, limit=6)
    if not candidates:
        raise LocationResolutionError(
            "Could not resolve the location. Try a city/state, ZIP code, or select a dropdown result."
        )

    best = candidates[0]
    if (
        best.source == "explicit"
        and best.match_reason in {"city_state", "zip_code"}
        and best.confidence >= 0.9
    ):
        return best.to_market()
    second = candidates[1] if len(candidates) > 1 else None
    has_clear_gap = second is None or best.confidence - second.confidence >= 0.12
    if best.confidence >= 0.9 and has_clear_gap:
        return best.to_market()

    raise LocationResolutionError(
        "That location is ambiguous. Select one of the suggested locations before scanning.",
        candidates=candidates,
    )


def normalize_country(value: str) -> str:
    cleaned = _normalize(value)
    return COUNTRY_ALIASES.get(cleaned, value.strip().upper()[:2] or "US")


def _explicit_candidates(query: str, country: str) -> list[LocationCandidate]:
    candidates: list[LocationCandidate] = []
    zip_match = re.fullmatch(r"(\d{5})(?:-\d{4})?", query.strip())
    if zip_match:
        postal_code = zip_match.group(1)
        candidates.append(
            LocationCandidate(
                id=f"{country.lower()}-zip-{postal_code}",
                label=f"ZIP {postal_code}, {country}",
                type=LocationType.postal_code,
                country=country,
                postal_code=postal_code,
                source="explicit",
                confidence=0.95,
                match_reason="zip_code",
            )
        )

    parts = [part.strip() for part in query.split(",") if part.strip()]
    if len(parts) >= 2:
        parsed_country = normalize_country(parts[2]) if len(parts) >= 3 else country
        state = _normalize_state(parts[1])
        city = _title_place(parts[0])
        if state and parsed_country == "US":
            candidates.append(
                LocationCandidate(
                    id=f"{slugify(city)}-{state.lower()}-{parsed_country.lower()}",
                    label=f"{city}, {state}, {parsed_country}",
                    type=LocationType.city,
                    country=parsed_country,
                    state=state,
                    city=city,
                    source="explicit",
                    confidence=0.96,
                    match_reason="city_state",
                )
            )
        elif parsed_country:
            candidates.append(
                LocationCandidate(
                    id=f"{slugify(city)}-{parsed_country.lower()}",
                    label=f"{city}, {parsed_country}",
                    type=LocationType.city,
                    country=parsed_country,
                    city=city,
                    source="explicit",
                    confidence=0.88,
                    match_reason="city_country",
                )
            )
    elif state_suffix := re.fullmatch(r"(.+?)\s+([A-Za-z]{2})", query.strip()):
        state = _normalize_state(state_suffix.group(2))
        city = _title_place(state_suffix.group(1))
        if state and country == "US":
            candidates.append(
                LocationCandidate(
                    id=f"{slugify(city)}-{state.lower()}-{country.lower()}",
                    label=f"{city}, {state}, {country}",
                    type=LocationType.city,
                    country=country,
                    state=state,
                    city=city,
                    source="explicit",
                    confidence=0.93,
                    match_reason="city_state",
                )
            )
    return candidates


def _seed_candidates(query: str, country: str, settings: Settings) -> list[LocationCandidate]:
    seed_path = settings.project_root / "seeds" / "locations.example.yaml"
    if not seed_path.exists():
        return []
    try:
        markets = load_markets(seed_path)
    except SeedValidationError:
        return []
    return [
        _candidate_from_market(market, "seed", _score(query, market.display_name), "seed_market")
        for market in markets
        if market.country_code.upper() == country and _score(query, market.display_name) >= 0.45
    ]


def _gazetteer_candidates(query: str, country: str, settings: Settings) -> list[LocationCandidate]:
    path = settings.project_root / "seeds" / "location_gazetteer.us.yaml"
    if country != "US" or not path.exists():
        return []
    try:
        payload = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return []

    candidates: list[LocationCandidate] = []
    for item in payload.get("locations") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("country_code") or "US").upper() != country:
            continue
        city = str(item.get("city") or "").strip()
        state = str(item.get("state") or "").strip().upper()
        if not city or not state:
            continue
        label = f"{city}, {state}, {country}"
        score = _score(query, label)
        if score < 0.55:
            continue
        candidates.append(
            LocationCandidate(
                id=f"{slugify(city)}-{state.lower()}-{country.lower()}",
                label=label,
                type=LocationType.city,
                country=country,
                state=state,
                city=city,
                latitude=_to_float(item.get("latitude")),
                longitude=_to_float(item.get("longitude")),
                source="gazetteer",
                confidence=min(0.93, max(0.68, score)),
                match_reason="local_city_index",
                resolution_metadata={"gazetteer_file": path.name},
            )
        )
    return candidates


def _db_market_candidates(session: Session, query: str, country: str) -> list[LocationCandidate]:
    rows = session.scalars(
        select(MarketORM).where(MarketORM.country_code == country).order_by(MarketORM.updated_at.desc())
    ).all()
    candidates: list[LocationCandidate] = []
    for row in rows:
        if not _market_country_consistent(row):
            continue
        score = _score(query, row.display_name)
        if score < 0.45:
            continue
        market = Market(
            id=row.slug,
            slug=row.slug,
            display_name=row.display_name,
            type=row.type,
            country_code=row.country_code,
            state=row.state,
            cities=row.cities,
            postal_codes=row.postal_codes,
            latitude=row.latitude,
            longitude=row.longitude,
            provider_location_code=row.provider_location_code,
            provider_location_name=row.provider_location_name,
            resolution_metadata=row.resolution_metadata,
        )
        candidates.append(_candidate_from_market(market, "database", score, "existing_market"))
    return candidates


def _cached_dataforseo_candidates(
    session: Session, query: str, country: str
) -> list[LocationCandidate]:
    rows = session.scalars(
        select(RawApiResponseORM)
        .where(RawApiResponseORM.endpoint.like("%/locations/%"))
        .order_by(RawApiResponseORM.response_time.desc())
        .limit(5)
    ).all()
    candidates: list[LocationCandidate] = []
    for row in rows:
        for item in _dataforseo_location_items(row.response_json):
            if str(item.get("country_iso_code") or "").upper() != country:
                continue
            location_name = str(item.get("location_name") or "").strip()
            if not location_name:
                continue
            score = _score(query, location_name)
            if score < 0.52 and _normalize(query) not in _normalize(location_name):
                continue
            city, state = _city_state_from_provider_location(location_name, country)
            candidates.append(
                LocationCandidate(
                    id=f"dataforseo-{item.get('location_code')}",
                    label=_provider_label(city, state, country, location_name),
                    type=_provider_location_type(item),
                    country=country,
                    state=state,
                    city=city,
                    provider_location_code=str(item.get("location_code") or ""),
                    provider_location_name=location_name,
                    source="dataforseo-cache",
                    confidence=min(0.94, max(0.55, score)),
                    match_reason="cached_provider_location",
                    resolution_metadata={
                        "provider": row.provider,
                        "provider_location_type": item.get("location_type"),
                        "provider_location_name": location_name,
                    },
                )
            )
    return candidates


async def _pelias_candidates(
    query: str, country: str, settings: Settings, limit: int
) -> list[LocationCandidate]:
    if not settings.pelias_base_url.strip():
        return []
    base_url = settings.pelias_base_url.rstrip("/")
    params = {
        "text": query,
        "size": str(limit),
        "boundary.country": country,
        "layers": "locality,localadmin,county,region,postalcode,neighbourhood",
    }
    try:
        async with httpx.AsyncClient(timeout=settings.location_search_timeout_seconds) as client:
            response = await client.get(f"{base_url}/v1/autocomplete", params=params)
            response.raise_for_status()
    except httpx.HTTPError:
        return []

    payload = response.json()
    candidates: list[LocationCandidate] = []
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        feature_country = normalize_country(str(props.get("country_a") or props.get("country") or country))
        if feature_country != country:
            continue
        layer = str(props.get("layer") or "locality")
        city = props.get("locality") or props.get("localadmin") or props.get("name")
        state = props.get("region_a") or props.get("region")
        postal_code = props.get("postalcode") if layer == "postalcode" else None
        label = str(props.get("label") or props.get("name") or query)
        candidates.append(
            LocationCandidate(
                id=f"pelias-{slugify(str(props.get('gid') or label))}",
                label=label,
                type=LocationType.postal_code if layer == "postalcode" else LocationType.city,
                country=feature_country,
                state=str(state).upper() if state and feature_country == "US" else state,
                city=str(city) if city else None,
                postal_code=str(postal_code) if postal_code else None,
                longitude=float(coords[0]) if len(coords) >= 2 else None,
                latitude=float(coords[1]) if len(coords) >= 2 else None,
                source="pelias",
                confidence=max(0.6, _score(query, label)),
                match_reason=f"pelias_{layer}",
                resolution_metadata={"pelias_gid": props.get("gid"), "pelias_layer": layer},
            )
        )
    return candidates


def _candidate_from_market(
    market: Market, source: str, score: float, match_reason: str
) -> LocationCandidate:
    return LocationCandidate(
        id=market.slug or slugify(market.id),
        label=market.display_name,
        type=market.type,
        country=market.country_code.upper(),
        state=market.state,
        city=market.cities[0] if market.cities else None,
        postal_code=market.postal_codes[0] if market.postal_codes else None,
        latitude=market.latitude,
        longitude=market.longitude,
        provider_location_code=market.provider_location_code,
        provider_location_name=market.provider_location_name,
        source=source,
        confidence=min(0.94, max(0.5, score)),
        match_reason=match_reason,
        resolution_metadata=market.resolution_metadata,
    )


def _rank_and_dedupe(
    query: str, candidates: list[LocationCandidate], limit: int
) -> list[LocationCandidate]:
    deduped: dict[str, LocationCandidate] = {}
    for candidate in candidates:
        key = "|".join(
            [
                _normalize(candidate.label),
                candidate.country,
                candidate.provider_location_code or "",
            ]
        )
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = candidate
        elif candidate.confidence > existing.confidence:
            deduped[key] = _merge_candidate_fields(candidate, existing)
        else:
            deduped[key] = _merge_candidate_fields(existing, candidate)
    normalized_query = _normalize(query)
    ranked = sorted(
        deduped.values(),
        key=lambda item: (
            item.confidence,
            1 if _normalize(item.label).startswith(normalized_query) else 0,
            1 if item.source == "pelias" else 0,
            -len(item.label),
        ),
        reverse=True,
    )
    return ranked[:limit]


def _merge_candidate_fields(
    preferred: LocationCandidate, fallback: LocationCandidate
) -> LocationCandidate:
    update: dict[str, Any] = {}
    for field_name in [
        "latitude",
        "longitude",
        "provider_location_code",
        "provider_location_name",
        "state",
        "city",
        "postal_code",
    ]:
        if getattr(preferred, field_name) is None and getattr(fallback, field_name) is not None:
            update[field_name] = getattr(fallback, field_name)
    if fallback.resolution_metadata:
        update["resolution_metadata"] = {
            **fallback.resolution_metadata,
            **preferred.resolution_metadata,
        }
    return preferred.model_copy(update=update) if update else preferred


def _dataforseo_location_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for task in payload.get("tasks") or []:
        result = task.get("result")
        if isinstance(result, list):
            items.extend([item for item in result if isinstance(item, dict)])
    return items


def _provider_location_type(item: dict[str, Any]) -> LocationType:
    location_type = str(item.get("location_type") or "").lower()
    if "postal" in location_type or "zip" in location_type:
        return LocationType.postal_code
    if "county" in location_type:
        return LocationType.county
    return LocationType.city


def _city_state_from_provider_location(
    location_name: str, country: str
) -> tuple[str | None, str | None]:
    parts = [part.strip() for part in location_name.split(",") if part.strip()]
    city = parts[0] if parts else None
    state: str | None = None
    if country == "US" and len(parts) >= 2:
        state = _normalize_state(parts[1])
    return city, state


def _provider_label(city: str | None, state: str | None, country: str, fallback: str) -> str:
    if city and state:
        return f"{city}, {state}, {country}"
    if city:
        return f"{city}, {country}"
    return fallback


def _infer_provider_location_name(
    *,
    city: str | None,
    state: str | None,
    country: str,
    location_type: LocationType,
) -> str | None:
    if location_type != LocationType.city or not city:
        return None
    if country == "US" and state:
        state_name = STATE_NAMES.get(state.upper()) or state
        return f"{city},{state_name},{COUNTRY_DISPLAY_NAMES['US']}"
    country_name = COUNTRY_DISPLAY_NAMES.get(country)
    if country_name:
        return f"{city},{country_name}"
    return None


def _market_country_consistent(row: MarketORM) -> bool:
    expected = row.country_code.upper()
    provider_country = _provider_name_country(row.provider_location_name)
    metadata = row.resolution_metadata or {}
    matched_country = _provider_name_country(str(metadata.get("matched_location") or ""))
    return all(country in {"", expected} for country in {provider_country, matched_country})


def _provider_name_country(value: str | None) -> str:
    if not value:
        return ""
    parts = [_normalize(part) for part in value.split(",") if part.strip()]
    if not parts:
        return ""
    return COUNTRY_ALIASES.get(parts[-1], "")


def _normalize_state(value: str) -> str | None:
    cleaned = _normalize(value)
    upper = value.strip().upper()
    if upper in STATE_NAMES:
        return upper
    return STATE_ABBREVIATIONS.get(cleaned)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _title_place(value: str) -> str:
    abbreviations = {"st": "St.", "ft": "Ft."}
    words = []
    for word in value.strip().split():
        cleaned = word.strip(".").lower()
        words.append(abbreviations.get(cleaned, word.capitalize()))
    return " ".join(words)


def _score(query: str, label: str) -> float:
    normalized_query = _normalize(query)
    normalized_label = _normalize(label)
    if not normalized_query or not normalized_label:
        return 0
    if normalized_query == normalized_label:
        return 1
    if normalized_label.startswith(normalized_query):
        return 0.92
    if normalized_query in normalized_label:
        return 0.82
    query_tokens = set(normalized_query.split())
    label_tokens = set(normalized_label.split())
    overlap = len(query_tokens.intersection(label_tokens)) / max(1, len(query_tokens))
    ratio = SequenceMatcher(None, normalized_query, normalized_label).ratio()
    return max(ratio, overlap * 0.85)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
