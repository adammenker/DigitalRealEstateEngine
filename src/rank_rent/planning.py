from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import RawApiResponseORM
from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.integrations.dataforseo.live import (
    CITY_COORDINATES,
    DataForSEOLiveProvider,
    dataforseo_provider_name,
    normalize_dataforseo_environment,
)
from rank_rent.runtime import DataMode
from rank_rent.services.cache import cache_key, normalize_request
from rank_rent.settings import Settings


class PlannedApiCall(BaseModel):
    provider: str
    endpoint: str
    request_parameters: dict[str, Any]
    cache_key: str
    cache_hit: bool = False
    request_known: bool = True
    estimated_cost_usd: Decimal = Decimal("0")
    required: bool = True
    stage: str


class ScanPlan(BaseModel):
    scan_profile: str
    planned_calls: list[PlannedApiCall] = Field(default_factory=list)
    cache_hit_count: int = 0
    paid_call_count: int = 0
    cached_cost_usd: Decimal = Decimal("0")
    estimated_uncached_cost_usd: Decimal = Decimal("0")
    maximum_allowed_cost_usd: Decimal = Decimal("0")
    maximum_request_count: int = 0
    confirmation_required: bool = False
    blocked: bool = False
    block_reason: str | None = None


def build_scan_plan(
    settings: Settings,
    mode: DataMode,
    service: ServiceFamily,
    market: Market,
    session: Session | None = None,
) -> ScanPlan:
    maximum = Decimal(str(settings.max_scan_cost_usd))
    if mode != DataMode.live:
        return ScanPlan(
            scan_profile=settings.live_scan_depth,
            maximum_allowed_cost_usd=maximum,
            maximum_request_count=settings.max_scan_requests,
        )

    profile = settings.live_scan_depth.lower().strip()
    provider = dataforseo_provider_name(settings)
    free_sandbox = normalize_dataforseo_environment(settings) == "sandbox"
    planned: list[PlannedApiCall] = []

    if not market.provider_location_code and not market.provider_location_name:
        _append_call(
            planned,
            session=session,
            provider=provider,
            endpoint="/v3/serp/google/locations/us",
            stage="location_resolution",
            params={},
            cost="0",
            required=True,
        )

    first_seed = _keyword_seeds(service)[0]
    keyword_task = {
        "keyword": first_seed,
        "language_code": "en",
        "limit": 10 if profile == "testing" else 20,
        "include_seed_keyword": True,
        "location_code": DataForSEOLiveProvider.us_labs_location_code,
    }
    _append_call(
            planned,
            session=session,
            provider=provider,
            endpoint="/v3/dataforseo_labs/google/keyword_suggestions/live",
        stage="keyword_discovery",
        params={"tasks": [keyword_task]},
        cost="0" if free_sandbox else "0.012",
        required=True,
    )

    _append_call(
        planned,
        session=session,
        provider=provider,
        endpoint="/v3/dataforseo_labs/google/historical_search_volume/live",
        stage="keyword_metrics",
        params={
            "tasks": [
                {
                    "keywords": ["<from keyword discovery>"],
                    "language_code": "en",
                    "location_code": DataForSEOLiveProvider.us_labs_location_code,
                }
            ]
        },
        cost="0" if free_sandbox else "0.012",
        required=True,
        request_known=False,
    )

    serp_count = 1 if profile == "testing" else 3
    for slot in range(serp_count):
        _append_call(
            planned,
            session=session,
            provider=provider,
            endpoint="/v3/serp/google/organic/live/advanced",
            stage="serp",
            params={
                "tasks": [
                    {
                        "keyword": f"<representative keyword {slot + 1}>",
                        "language_code": "en",
                        "device": "desktop",
                        "depth": 10,
                        **_location_payload(market),
                    }
                ]
            },
            cost="0" if free_sandbox else "0.002",
            required=True,
            request_known=False,
        )

    for slot in range(0 if profile == "testing" else 5):
        _append_call(
            planned,
            session=session,
            provider=provider,
            endpoint="/v3/backlinks/summary/live",
            stage="competitors",
            params={
                "tasks": [
                    {
                        "target": f"<organic competitor domain {slot + 1}>",
                        "include_subdomains": True,
                    }
                ]
            },
            cost="0" if free_sandbox else "0.02",
            required=False,
            request_known=False,
        )

    provider_task: dict[str, Any] = {
        "language_code": "en",
        "limit": 5 if profile == "testing" else 10,
        "filters": ["address_info.country_code", "=", market.country_code.upper()],
        "description": f"{service.display_name} {market.display_name}",
    }
    coordinate = _location_coordinate(market)
    if coordinate:
        provider_task["location_coordinate"] = coordinate
    if service.provider_categories:
        provider_task["categories"] = service.provider_categories[:10]
    _append_call(
        planned,
        session=session,
        provider=provider,
        endpoint="/v3/business_data/business_listings/search/live",
        stage="provider_discovery",
        params={"tasks": [provider_task]},
        cost="0" if free_sandbox else "0.01",
        required=True,
        request_known=True,
    )

    uncached = sum(
        (call.estimated_cost_usd for call in planned if not call.cache_hit),
        Decimal("0"),
    )
    cached = sum(
        (call.estimated_cost_usd for call in planned if call.cache_hit),
        Decimal("0"),
    )
    paid_call_count = len([call for call in planned if not call.cache_hit and call.estimated_cost_usd > 0])
    over_budget = uncached > maximum
    over_request_limit = len(planned) > settings.max_scan_requests
    return ScanPlan(
        scan_profile=profile,
        planned_calls=planned,
        cache_hit_count=len([call for call in planned if call.cache_hit]),
        paid_call_count=paid_call_count,
        cached_cost_usd=cached,
        estimated_uncached_cost_usd=uncached,
        maximum_allowed_cost_usd=maximum,
        maximum_request_count=settings.max_scan_requests,
        confirmation_required=uncached > 0,
        blocked=over_budget or over_request_limit,
        block_reason=(
            f"Estimated uncached API cost ${uncached} exceeds MAX_SCAN_COST_USD ${maximum}."
            if over_budget
            else f"Planned API request count {len(planned)} exceeds MAX_SCAN_REQUESTS {settings.max_scan_requests}."
            if over_request_limit
            else None
        ),
    )


def _append_call(
    planned: list[PlannedApiCall],
    *,
    session: Session | None,
    provider: str,
    endpoint: str,
    stage: str,
    params: dict[str, Any],
    cost: str,
    required: bool,
    request_known: bool = True,
) -> None:
    normalized = normalize_request(params)
    key = cache_key(provider, endpoint, normalized, "v3")
    hit = _cache_hit(session, key) if request_known else False
    planned.append(
        PlannedApiCall(
            provider=provider,
            endpoint=endpoint,
            request_parameters=normalized,
            cache_key=key,
            cache_hit=hit,
            request_known=request_known,
            estimated_cost_usd=Decimal(cost),
            stage=stage,
            required=required,
        )
    )


def _cache_hit(session: Session | None, key: str) -> bool:
    if session is None:
        return False
    return session.scalar(select(RawApiResponseORM.id).where(RawApiResponseORM.cache_key == key)) is not None


def _keyword_seeds(service: ServiceFamily) -> list[str]:
    base = service.display_name.lower()
    seeds = list(service.seed_queries or [])
    seeds.extend([f"{base} contractor", f"{base} repair", f"{base} installation", base])
    deduped: list[str] = []
    for seed in seeds:
        normalized = " ".join(seed.lower().split())
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped or [base]


def _location_payload(market: Market) -> dict[str, Any]:
    if market.provider_location_code:
        return {"location_code": int(market.provider_location_code)}
    if market.provider_location_name:
        return {"location_name": market.provider_location_name}
    return {"location_name": market.display_name}


def _location_coordinate(market: Market, radius_km: int = 50) -> str | None:
    if market.latitude is not None and market.longitude is not None:
        return f"{market.latitude:.6f},{market.longitude:.6f},{radius_km}"
    normalized = _normalize_location(market.display_name)
    coordinates = CITY_COORDINATES.get(normalized)
    if coordinates is None and market.cities:
        coordinates = CITY_COORDINATES.get(
            _normalize_location(f"{market.cities[0]} {market.state or ''}")
        )
    if coordinates is None:
        return None
    return f"{coordinates[0]:.6f},{coordinates[1]:.6f},{radius_km}"


def _normalize_location(value: str) -> str:
    return " ".join(value.lower().replace(".", "").replace(",", " ").replace("-", " ").split())
