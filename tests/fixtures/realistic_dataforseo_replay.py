from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.integrations.dataforseo.live import (
    DataForSEOLiveProvider,
    business_listings_location_payload,
    serp_location_payload,
)
from rank_rent.replay import StoredApiResponse
from rank_rent.services.cache import checksum_payload, normalize_request

CAPTURED_AT = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
PROVIDER = "dataforseo-live"


def realistic_response_bundle(
    service: ServiceFamily,
    market: Market,
) -> dict[str, Any]:
    responses: list[StoredApiResponse] = []
    suggestion_items = [
        {"keyword": "emergency water heater repair"},
        {"keyword": "tankless water heater installation"},
        {"keyword": "water heater repair"},
        {"keyword": "water heater repair diy"},
    ]
    for seed in service.seed_queries[:3]:
        responses.append(
            _response(
                "/v3/dataforseo_labs/google/keyword_suggestions/live",
                {
                    "tasks": [
                        {
                            "keyword": seed,
                            "language_code": "en",
                            "limit": 20,
                            "include_seed_keyword": True,
                            "location_code": DataForSEOLiveProvider.us_labs_location_code,
                        }
                    ]
                },
                _items_payload(suggestion_items, "suggestions"),
            )
        )

    metric_values = {
        "emergency water heater repair": (1100, 34.5, 0.82),
        "tankless water heater installation": (650, 31.0, 0.69),
        "water heater repair": (900, 26.0, 0.74),
    }
    metric_items = [
        {
            "keyword": keyword,
            "keyword_info": {
                "search_volume": volume,
                "cpc": cpc,
                "competition": competition,
                "monthly_searches": [
                    {
                        "year": 2026,
                        "month": month,
                        "search_volume": volume + ((month - 6) * 10),
                    }
                    for month in range(1, 13)
                ],
            },
            "search_intent_info": {"main_intent": "transactional"},
        }
        for keyword, (volume, cpc, competition) in metric_values.items()
    ]
    metric_keywords = [
        "emergency water heater repair",
        "water heater repair",
        "tankless water heater installation",
    ]
    responses.append(
        _response(
            "/v3/dataforseo_labs/google/historical_search_volume/live",
            {
                "tasks": [
                    {
                        "keywords": metric_keywords,
                        "language_code": "en",
                        "location_code": DataForSEOLiveProvider.us_labs_location_code,
                    }
                ]
            },
            _items_payload(metric_items, "metrics"),
        )
    )

    representative_keywords = [
        "emergency water heater repair",
        "water heater repair",
        "tankless water heater installation",
    ]
    for keyword in representative_keywords:
        responses.append(
            _response(
                "/v3/serp/google/organic/live/advanced",
                {
                    "tasks": [
                        {
                            "keyword": keyword,
                            "language_code": "en",
                            "device": "desktop",
                            "depth": 10,
                            **serp_location_payload(market),
                        }
                    ]
                },
                {
                    "tasks": [
                        {
                            "id": f"serp-{keyword}",
                            "status_code": 20000,
                            "status_message": "Ok.",
                            "cost": 0,
                            "result": [{"keyword": keyword, "items": _serp_items(keyword)}],
                        }
                    ]
                },
            )
        )

    backlink_values = {
        "stlwaterheaterpros.com": (42, 390, 128),
        "yelp.com": (820000, 15000000, 793),
        "taskrabbit.com": (62000, 1100000, 575),
        "homedepot.com": (1200000, 24000000, 872),
        "waterheaterquotes.example": (18, 74, 91),
    }
    representative_query = representative_keywords[0].replace(" ", "-")
    competitor_urls = {
        "stlwaterheaterpros.com": (
            "https://stlwaterheaterpros.com/emergency-water-heater-repair-st-louis"
        ),
        "yelp.com": f"https://www.yelp.com/search/{representative_query}-st-louis",
        "taskrabbit.com": (
            f"https://www.taskrabbit.com/locations/st-louis/{representative_query}"
        ),
        "homedepot.com": "https://www.homedepot.com/services/c/water-heater-repair",
        "waterheaterquotes.example": (
            f"https://waterheaterquotes.example/{representative_query}-st-louis"
        ),
    }
    for domain, (referring_domains, backlinks, rank) in backlink_values.items():
        responses.append(
            _response(
                "/v3/backlinks/summary/live",
                {"tasks": [{"target": domain, "include_subdomains": True}]},
                {
                    "tasks": [
                        {
                            "id": f"backlinks-{domain}",
                            "status_code": 20000,
                            "status_message": "Ok.",
                            "cost": 0,
                            "result": [
                                {
                                    "target": domain,
                                    "referring_domains": referring_domains,
                                    "backlinks": backlinks,
                                    "rank": rank,
                                    "source_url": competitor_urls[domain],
                                }
                            ],
                        }
                    ]
                },
            )
        )

    provider_task = {
        "language_code": "en",
        "limit": 10,
        "filters": ["address_info.country_code", "=", "US"],
        "description": f"{service.display_name} {market.display_name}",
        "categories": service.provider_categories,
        **business_listings_location_payload(market, "production"),
    }
    responses.append(
        _response(
            "/v3/business_data/business_listings/search/live",
            {"tasks": [provider_task]},
            _items_payload(_provider_items(), "providers"),
        )
    )
    return {
        "exported_at": CAPTURED_AT.isoformat(),
        "source_scan_run_id": 4102,
        "responses": [response.model_dump(mode="json") for response in responses],
    }


def _response(
    endpoint: str,
    request: dict[str, Any],
    raw_response: dict[str, Any],
) -> StoredApiResponse:
    return StoredApiResponse(
        provider=PROVIDER,
        endpoint=endpoint,
        api_version="v3",
        normalized_request=normalize_request(request),
        raw_response=raw_response,
        sanitized=True,
        provider_cost_usd=Decimal("0"),
        requested_at=CAPTURED_AT,
        received_at=CAPTURED_AT,
        source_scan_run_id=4102,
        checksum=checksum_payload(raw_response),
    )


def _items_payload(items: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
    return {
        "tasks": [
            {
                "id": task_id,
                "status_code": 20000,
                "status_message": "Ok.",
                "cost": 0,
                "result": [{"items": items}],
            }
        ]
    }


def _serp_items(keyword: str) -> list[dict[str, Any]]:
    slug = keyword.replace(" ", "-")
    return [
        {
            "type": "paid",
            "rank_absolute": 1,
            "url": "https://ads.example.test/st-louis-water-heaters",
            "domain": "ads.example.test",
            "title": "St. Louis Water Heater Service Ad",
        },
        {
            "type": "local_pack",
            "rank_absolute": 2,
            "url": "https://maps.example.test/st-louis-plumbers",
            "domain": "maps.example.test",
            "title": "St. Louis plumbers",
        },
        {
            "type": "organic",
            "rank_absolute": 1,
            "url": f"https://stlwaterheaterpros.com/{slug}-st-louis",
            "domain": "stlwaterheaterpros.com",
            "title": f"{keyword.title()} in St. Louis | Licensed Local Plumbers",
            "description": (
                "Locally owned, licensed plumbers. Call now to schedule service in St. Louis, MO."
            ),
        },
        {
            "type": "organic",
            "rank_absolute": 2,
            "url": f"https://www.yelp.com/search/{slug}-st-louis",
            "domain": "yelp.com",
            "title": f"Best {keyword.title()} in St. Louis, MO",
            "description": "Reviews and listings for local businesses.",
        },
        {
            "type": "organic",
            "rank_absolute": 3,
            "url": f"https://www.taskrabbit.com/locations/st-louis/{slug}",
            "domain": "taskrabbit.com",
            "title": f"Hire help for {keyword} in St. Louis",
            "description": "Book a local tasker.",
        },
        {
            "type": "organic",
            "rank_absolute": 4,
            "url": "https://www.homedepot.com/services/c/water-heater-repair",
            "domain": "homedepot.com",
            "title": "Water Heater Repair Services",
            "description": "National installation and repair services.",
        },
        {
            "type": "organic",
            "rank_absolute": 5,
            "url": f"https://waterheaterquotes.example/{slug}-st-louis",
            "domain": "waterheaterquotes.example",
            "title": f"Get Free Quotes for {keyword.title()}",
            "description": "Get free quotes and get matched with St. Louis contractors.",
        },
        {
            "type": "organic",
            "rank_absolute": 6,
            "url": f"https://www.familyhandyman.com/article/{slug}-cost-guide",
            "domain": "familyhandyman.com",
            "title": f"{keyword.title()} Cost Guide",
            "description": "Average cost guide and how to plan the project.",
        },
    ]


def _provider_items() -> list[dict[str, Any]]:
    return [
        {
            "title": "STL Water Heater Pros",
            "url": "https://stlwaterheaterpros.com",
            "phone": "+1-314-555-0101",
            "address_info": {
                "address": "101 Market St",
                "city": "St. Louis",
                "region": "MO",
                "zip": "63101",
                "country_code": "US",
            },
            "category": "Plumber",
            "additional_categories": ["Water heater installation service"],
            "latitude": 38.6304,
            "longitude": -90.2008,
            "rating": {"value": 4.8, "votes_count": 126},
            "work_time": {"work_hours": {"current_status": "open"}},
            "last_updated_time": "2026-06-30T12:00:00Z",
        },
        {
            "title": "Gateway Plumbing & Water Heaters",
            "url": "https://gatewayplumbing.example",
            "contact_info": [
                {"type": "phone", "value": "+1-314-555-0102"},
                {"type": "email", "value": "service@gatewayplumbing.example"},
            ],
            "address_info": {
                "address": "2200 Hampton Ave",
                "city": "St. Louis",
                "region": "MO",
                "zip": "63139",
                "country_code": "US",
            },
            "category": "Water heater installation service",
            "additional_categories": ["Plumber"],
            "latitude": 38.6124,
            "longitude": -90.2867,
            "rating": {"value": 4.7, "votes_count": 88},
            "work_time": {"work_hours": {"current_status": "open"}},
            "last_updated_time": "2026-06-29T10:00:00Z",
        },
        {
            "title": "Arch City Plumbing",
            "url": "https://archcityplumbing.example",
            "phone": "+1-314-555-0103",
            "address_info": {
                "address": "3100 Arsenal St",
                "city": "St. Louis",
                "region": "MO",
                "zip": "63118",
                "country_code": "US",
            },
            "category": "Plumber",
            "services": [{"title": "Water heater repair", "category": "Plumber"}],
            "latitude": 38.5987,
            "longitude": -90.2381,
            "rating": {"value": 4.5, "votes_count": 54},
            "work_time": {"work_hours": {"current_status": "open"}},
            "last_updated_time": "2026-06-28T09:00:00Z",
        },
        {
            "title": "Metro Appliance Outlet",
            "url": "https://metroappliances.example",
            "address_info": {
                "address": "9000 Page Ave",
                "city": "Overland",
                "region": "MO",
                "zip": "63114",
                "country_code": "US",
            },
            "category": "Appliance store",
            "latitude": 38.685,
            "longitude": -90.36,
            "rating": {"value": 3.9, "votes_count": 18},
            "work_time": {"work_hours": {"current_status": "unknown"}},
            "last_updated_time": "2026-06-20T09:00:00Z",
        },
        {
            "title": "Old Town Plumbing",
            "address_info": {
                "address": "10 Main St",
                "city": "St. Charles",
                "region": "MO",
                "zip": "63301",
                "country_code": "US",
            },
            "category": "Plumber",
            "latitude": 38.783,
            "longitude": -90.481,
            "rating": {"value": 4.0, "votes_count": 9},
            "work_time": {"work_hours": {"current_status": "closed"}},
            "last_updated_time": "2026-05-01T09:00:00Z",
        },
    ]
