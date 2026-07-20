from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.integrations.dataforseo.live import (
    DataForSEOLiveProvider,
    business_listings_location_payload,
    dataforseo_provider_name,
    normalize_dataforseo_environment,
    serp_location_payload,
)
from rank_rent.runtime import DataMode
from rank_rent.services.cache import cache_key, normalize_request, valid_cached_response
from rank_rent.services.keywords import service_seed_keywords
from rank_rent.services.us_geography import validate_market_against_index
from rank_rent.settings import Settings
from rank_rent.storage.blobs import BlobStore, build_blob_store


class PlannedApiCall(BaseModel):
    planned_request_id: str
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
    scan_profile: str | None = None,
) -> ScanPlan:
    maximum = Decimal(str(settings.max_scan_cost_usd))
    profile = _scan_profile(scan_profile or settings.live_scan_depth)
    if mode != DataMode.live:
        return ScanPlan(
            scan_profile=profile,
            maximum_allowed_cost_usd=maximum,
            maximum_request_count=settings.max_scan_requests,
        )

    switch_reason = _live_switch_block_reason(settings, profile)
    if switch_reason:
        return ScanPlan(
            scan_profile=profile,
            maximum_allowed_cost_usd=maximum,
            maximum_request_count=settings.max_scan_requests,
            blocked=True,
            block_reason=switch_reason,
        )

    validate_market_against_index(market, settings)
    provider = dataforseo_provider_name(settings)
    api_environment = normalize_dataforseo_environment(settings)
    free_sandbox = api_environment == "sandbox"
    planned: list[PlannedApiCall] = []
    blob_store = build_blob_store(settings) if session is not None else None

    keyword_seed_limit = 1 if profile == "testing" else 3
    keyword_suggestion_limit = 10 if profile == "testing" else 20
    for seed in _keyword_seeds(service)[:keyword_seed_limit]:
        keyword_task = {
            "keyword": seed,
            "language_code": "en",
            "limit": keyword_suggestion_limit,
            "include_seed_keyword": True,
            **_labs_location_payload(market),
        }
        _append_call(
            planned,
            session=session,
            blob_store=blob_store,
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
        blob_store=blob_store,
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
            blob_store=blob_store,
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
                        **serp_location_payload(market),
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
            blob_store=blob_store,
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
        **business_listings_location_payload(market, api_environment),
    }
    if service.provider_categories:
        provider_task["categories"] = service.provider_categories[:10]
    _append_call(
        planned,
        session=session,
        blob_store=blob_store,
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
    paid_call_count = len(
        [call for call in planned if not call.cache_hit and call.estimated_cost_usd > 0]
    )
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
    blob_store: BlobStore | None,
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
    hit = _cache_hit(session, key, blob_store) if request_known else False
    request_id = f"req-{len(planned) + 1:03d}"
    planned.append(
        PlannedApiCall(
            planned_request_id=request_id,
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


def _cache_hit(
    session: Session | None,
    key: str,
    blob_store: BlobStore | None,
) -> bool:
    if session is None:
        return False
    return valid_cached_response(session, key, blob_store=blob_store)


def _keyword_seeds(service: ServiceFamily) -> list[str]:
    return service_seed_keywords(service)


def _labs_location_payload(market: Market) -> dict[str, Any]:
    if market.country_code.upper() == "US":
        return {"location_code": DataForSEOLiveProvider.us_labs_location_code}
    return {"location_name": market.country_code.upper()}


def _scan_profile(value: str) -> str:
    profile = value.lower().strip()
    if profile not in {"testing", "full"}:
        raise ValueError("scan_profile must be 'testing' or 'full'.")
    return profile


def _live_switch_block_reason(settings: Settings, profile: str) -> str | None:
    if not settings.allow_live_api_calls:
        return "Live calls are disabled by ALLOW_LIVE_API_CALLS."
    if settings.paid_call_kill_switch:
        return "Paid calls are disabled by PAID_CALL_KILL_SWITCH."
    if (
        normalize_dataforseo_environment(settings) == "production"
        and not settings.allow_production_dataforseo
    ):
        return "Production DataForSEO calls require ALLOW_PRODUCTION_DATAFORSEO=true."
    if profile == "full" and not settings.allow_full_scans:
        return "Full scans require ALLOW_FULL_SCANS=true."
    return None
