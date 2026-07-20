import asyncio
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import yaml

from rank_rent.domain.models import Market, ProviderCandidate, ServiceFamily
from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider
from rank_rent.services.locations import market_from_geography_record
from rank_rent.services.providers import (
    provider_suitability_summary,
    score_provider_suitability,
)
from rank_rent.services.us_geography import USGeographyIndex
from rank_rent.settings import Settings


def _config() -> dict:
    return deepcopy(yaml.safe_load(Path("config/scoring.yaml").read_text())["providers"])


def _service() -> ServiceFamily:
    return ServiceFamily(
        id="water-heater-services",
        display_name="Water Heater Services",
        seed_queries=["water heater repair"],
        provider_categories=["plumber", "water heater installation service"],
    )


def _market() -> Market:
    index = USGeographyIndex(Path("data/us_geography.sqlite3"))
    return market_from_geography_record(index.search("06901", limit=1)[0].record)


def _provider(**updates) -> ProviderCandidate:
    values = {
        "name": "Menker Plumbing",
        "category": "Plumber",
        "categories": ["Plumber", "Emergency plumber"],
        "latitude": 41.06,
        "longitude": -73.54,
        "business_status": "open",
        "phone": "555-0100",
        "contact_confidence": 0.8,
        "rating": 4.7,
        "review_count": 80,
        "source": "dataforseo:business_listings",
    }
    values.update(updates)
    return ProviderCandidate(**values)


def test_provider_categories_match_service_without_display_name_overlap() -> None:
    scored = score_provider_suitability(
        [_provider()],
        _service(),
        _market(),
        _config(),
    )[0]

    assert scored.suitability_signals["service_fit"]["normalized"] == 1
    assert (
        scored.suitability_signals["service_fit"]["method"]
        == "configured_provider_category_exact"
    )
    assert scored.suitability_score is not None
    assert scored.suitability_score >= _config()["suitable_threshold"]


def test_unknown_status_scores_lower_than_confirmed_open_and_closed_is_ineligible() -> None:
    config = _config()
    open_provider, unknown_provider, closed_provider = score_provider_suitability(
        [
            _provider(),
            _provider(name="Unknown Plumbing", business_status="unknown"),
            _provider(name="Closed Plumbing", business_status="closed_forever"),
        ],
        _service(),
        _market(),
        config,
    )

    assert (
        open_provider.suitability_signals["status_certainty"]["normalized"] == 1
    )
    assert (
        unknown_provider.suitability_signals["status_certainty"]["normalized"]
        == 0.25
    )
    assert open_provider.suitability_score > unknown_provider.suitability_score
    assert closed_provider.suitability_score <= config["inactive_score_cap"]
    summary = provider_suitability_summary(
        [open_provider, unknown_provider, closed_provider],
        config,
    )
    assert summary["suitable_provider_count"] == 2


def test_dataforseo_closed_now_is_distinct_from_raw_close_status() -> None:
    config = _config()
    closed_now, raw_close = score_provider_suitability(
        [
            _provider(business_status="closed_now"),
            _provider(name="Unnormalized Status Plumbing", business_status="close"),
        ],
        _service(),
        _market(),
        config,
    )

    assert "close" not in config["status_scores"]
    assert closed_now.suitability_signals["status_certainty"]["normalized"] == 1
    assert raw_close.suitability_signals["status_certainty"]["normalized"] == 0.25


def test_contact_channels_are_one_composite_signal_not_additive_points() -> None:
    config = _config()
    email_only, every_channel = score_provider_suitability(
        [
            _provider(phone=None, email="owner@example.com"),
            _provider(
                name="All Channels Plumbing",
                website="https://plumber.example",
                email="owner@example.com",
                contact_form_url="https://plumber.example/contact",
            ),
        ],
        _service(),
        _market(),
        config,
    )

    assert (
        email_only.suitability_signals["contactability"]["normalized"]
        == every_channel.suitability_signals["contactability"]["normalized"]
    )


def test_coordinate_distance_and_verified_service_area_are_distinct_evidence() -> None:
    config = _config()
    nearby, distant, serves_market = score_provider_suitability(
        [
            _provider(),
            _provider(name="Distant Plumbing", latitude=44.0, longitude=-73.54),
            _provider(
                name="Service Area Plumbing",
                latitude=44.0,
                longitude=-73.54,
                service_area="Stamford, CT",
            ),
        ],
        _service(),
        _market(),
        config,
    )

    assert nearby.suitability_signals["geographic_fit"]["normalized"] > 0.95
    assert distant.suitability_signals["geographic_fit"]["normalized"] == 0
    assert (
        serves_market.suitability_signals["geographic_fit"]["method"]
        == "verified_service_area"
    )
    assert serves_market.suitability_signals["geographic_fit"]["normalized"] == 1


def test_suitable_threshold_is_configuration_driven() -> None:
    config = _config()
    scored = score_provider_suitability(
        [_provider()],
        _service(),
        _market(),
        config,
    )[0]
    strict_config = deepcopy(config)
    strict_config["suitable_threshold"] = 99

    normal = provider_suitability_summary([scored], config)
    strict = provider_suitability_summary([scored], strict_config)

    assert normal["suitable_provider_count"] == 1
    assert strict["suitable_provider_count"] == 0


def test_summary_separates_suitable_quality_from_raw_listing_noise() -> None:
    config = _config()
    providers = [
        ProviderCandidate(
            name=f"Suitable Provider {index}",
            business_status="open",
            suitability_score=score,
        )
        for index, score in enumerate([100, 90, 80, 70, 60, 55], start=1)
    ]
    providers.append(
        ProviderCandidate(
            name="Closed Irrelevant Listing",
            business_status="closed_forever",
            suitability_score=10,
        )
    )

    summary = provider_suitability_summary(providers, config)

    assert summary["suitable_provider_count"] == 6
    assert summary["suitable_provider_share"] == 0.8571
    assert summary["median_suitable_provider_score"] == 75
    assert summary["average_top_suitable_provider_score"] == 80
    assert summary["top_suitable_sample_size"] == 5
    assert summary["raw_average_suitability_score"] == 66.43
    assert len(summary["top_suitable_providers"]) == 5
    assert all(
        provider["score"] >= config["suitable_threshold"]
        for provider in summary["top_suitable_providers"]
    )


def test_provider_signal_weights_are_configuration_driven() -> None:
    config = _config()
    config["signal_weights"] = {
        "service_fit": 0,
        "geographic_fit": 0,
        "status_certainty": 100,
        "contactability": 0,
        "reputation": 0,
    }
    open_provider, unknown_provider = score_provider_suitability(
        [
            _provider(),
            _provider(name="Unknown Plumbing", business_status="unknown"),
        ],
        _service(),
        _market(),
        config,
    )

    assert open_provider.suitability_score == 100
    assert unknown_provider.suitability_score == 25


def test_dataforseo_business_listing_normalizes_provider_evidence() -> None:
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        dataforseo_login="user",
        dataforseo_password="password",
        dataforseo_environment="sandbox",
    )
    provider = DataForSEOLiveProvider(settings=settings)
    provider._post = AsyncMock(
        return_value={
            "status_code": 20000,
            "tasks": [
                {
                    "status_code": 20000,
                    "result": [
                        {
                            "items": [
                                {
                                    "type": "business_listing",
                                    "title": "Menker Plumbing",
                                    "category": "Plumber",
                                    "category_ids": ["plumber"],
                                    "additional_categories": ["Water heater installation service"],
                                    "services": [
                                        {
                                            "category": "Plumbing",
                                            "title": "Water heater repair",
                                        }
                                    ],
                                    "address_info": {
                                        "address": "1 Main St",
                                        "city": "Stamford",
                                        "zip": "06901",
                                        "country_code": "US",
                                    },
                                    "latitude": 41.0534,
                                    "longitude": -73.5387,
                                    "phone": "555-0100",
                                    "url": "https://menker-plumbing.example",
                                    "rating": {"value": 4.8, "votes_count": 120},
                                    "work_time": {
                                        "work_hours": {"current_status": "close"}
                                    },
                                    "last_updated_time": "2026-07-01 12:00:00 +00:00",
                                }
                            ]
                        }
                    ],
                }
            ],
        }
    )

    providers = asyncio.run(provider.find_providers(_service(), _market()))

    assert len(providers) == 1
    request_task = provider._post.await_args.args[1][0]
    assert "location_coordinate" not in request_task
    result = providers[0]
    assert "Plumber" in result.categories
    assert "Water heater repair" in result.categories
    assert result.latitude == 41.0534
    assert result.longitude == -73.5387
    assert result.business_status == "closed_now"
    assert result.service_area is None
    assert result.source_timestamp.year == 2026
