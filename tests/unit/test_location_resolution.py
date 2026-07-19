from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider
from rank_rent.planning import build_scan_plan
from rank_rent.repositories import market_from_orm, upsert_market
from rank_rent.runtime import DataMode
from rank_rent.services.locations import (
    LocationResolutionError,
    resolve_market_for_scan,
    search_locations,
)
from rank_rent.services.us_geography import (
    USGeographyError,
    USGeographyIndex,
)
from rank_rent.settings import Settings

ROOT = Path(__file__).parents[2]


def make_session() -> sessionmaker:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def settings(**updates: object) -> Settings:
    return Settings(project_root=ROOT, **updates)


def live_settings(**updates: object) -> Settings:
    return settings(
        data_mode="live",
        allow_live_api_calls=True,
        dataforseo_login="user",
        dataforseo_password="password",
        dataforseo_environment="production",
        **updates,
    )


def resolve(query: str, *, selected=None) -> Market:
    Session = make_session()
    with Session() as session:
        return asyncio.run(
            resolve_market_for_scan(
                session,
                query,
                "US",
                settings(),
                selected_location=selected,
            )
        )


def test_city_state_resolves_to_complete_canonical_market() -> None:
    market = resolve("London KY")

    assert market.display_name == "London, KY, US"
    assert market.geography_id == "place:2147476"
    assert market.geography_dataset_version == "us-geography-2024.1"
    assert market.state == "KY"
    assert market.cities == ["London"]
    assert market.county == "Laurel County"
    assert market.county_fips == "21125"
    assert market.metro == "Corbin, KY"
    assert market.latitude is not None
    assert market.longitude is not None
    assert market.population == 7_561
    assert market.reference_population == 334_922_499
    assert market.boundary_radius_km == 3.24
    assert market.provider_location_name == "London,Kentucky,United States"


def test_zip_resolves_with_city_county_metro_population_and_boundary() -> None:
    market = resolve("63101")

    assert market.display_name == "ZIP 63101 - St. Louis, MO, US"
    assert market.geography_id == "zcta:63101"
    assert market.postal_codes == ["63101"]
    assert market.cities == ["St. Louis"]
    assert market.county == "St. Louis city"
    assert market.metro == "St. Louis, MO-IL"
    assert market.population == 3_130
    assert market.boundary_radius_km == 2.0


@pytest.mark.parametrize("query", ["London", "Springfield", "Portland", "Columbus"])
def test_ambiguous_city_requires_dropdown_selection(query: str) -> None:
    Session = make_session()
    with Session() as session:
        with pytest.raises(LocationResolutionError, match="ambiguous") as captured:
            asyncio.run(resolve_market_for_scan(session, query, "US", settings()))

    assert len(captured.value.candidates) >= 2
    assert len({candidate.state for candidate in captured.value.candidates}) >= 2


def test_selected_location_is_reloaded_from_index_instead_of_trusting_client_fields() -> None:
    Session = make_session()
    with Session() as session:
        options = asyncio.run(search_locations(session, "London", "US", settings(), limit=12))
        selected = next(option for option in options if option.state == "KY")
        tampered = selected.model_copy(
            update={
                "latitude": 0.0,
                "longitude": 0.0,
                "population": 1,
                "boundary_radius_km": 999.0,
            }
        )
        market = asyncio.run(
            resolve_market_for_scan(
                session,
                "London",
                "US",
                settings(),
                selected_location=tampered,
            )
        )

    assert market.latitude == selected.latitude
    assert market.longitude == selected.longitude
    assert market.population == 7_561
    assert market.boundary_radius_km == 3.24
    assert market.resolution_metadata["selected_location_match_reason"] == (
        "selected_canonical_location"
    )


def test_unknown_zip_and_non_us_market_are_rejected() -> None:
    Session = make_session()
    with Session() as session:
        with pytest.raises(LocationResolutionError, match="Could not resolve"):
            asyncio.run(resolve_market_for_scan(session, "99999", "US", settings()))
        with pytest.raises(LocationResolutionError, match="U.S. cities and ZIP"):
            asyncio.run(resolve_market_for_scan(session, "London", "GB", settings()))


def test_typo_search_returns_canonical_clickable_result() -> None:
    Session = make_session()
    with Session() as session:
        results = asyncio.run(search_locations(session, "Stamfrd CT", "US", settings()))

    assert results
    assert results[0].label == "Stamford, CT, US"
    assert results[0].match_reason == "fuzzy_city"
    assert results[0].county == "Fairfield County"


def test_live_plan_requires_canonical_geography_and_always_plans_provider_radius() -> None:
    market = resolve("London KY")
    plan = build_scan_plan(
        live_settings(),
        DataMode.live,
        service=ServiceFamily(
            id="water-heater-repair",
            display_name="Water Heater Repair",
            seed_queries=["water heater repair"],
        ),
        market=market,
    )

    assert "location_resolution" not in [call.stage for call in plan.planned_calls]
    serp_call = next(call for call in plan.planned_calls if call.stage == "serp")
    assert serp_call.request_parameters["tasks"][0]["location_name"] == (
        "London,Kentucky,United States"
    )
    provider_call = next(
        call for call in plan.planned_calls if call.stage == "provider_discovery"
    )
    assert provider_call.request_parameters["tasks"][0]["location_coordinate"] == (
        f"{market.latitude:.6f},{market.longitude:.6f},3.24"
    )


def test_live_plan_rejects_unresolved_or_stale_market() -> None:
    unresolved = Market(id="st-louis", display_name="St. Louis, MO")
    with pytest.raises(USGeographyError, match="not linked"):
        build_scan_plan(
            live_settings(),
            DataMode.live,
            ServiceFamily(id="plumbing", display_name="Plumbing"),
            unresolved,
        )

    stale = resolve("St. Louis MO").model_copy(update={"population": 1})
    with pytest.raises(USGeographyError, match="stale or invalid population"):
        build_scan_plan(
            live_settings(),
            DataMode.live,
            ServiceFamily(id="plumbing", display_name="Plumbing"),
            stale,
        )


def test_provider_discovery_rejects_missing_boundary_before_network() -> None:
    provider = DataForSEOLiveProvider(settings=live_settings())
    provider._post = AsyncMock()  # type: ignore[method-assign]
    unresolved = Market(id="st-louis", display_name="St. Louis, MO")

    with pytest.raises(USGeographyError, match="not linked"):
        asyncio.run(
            provider.find_providers(
                ServiceFamily(id="plumbing", display_name="Plumbing"),
                unresolved,
            )
        )

    provider._post.assert_not_awaited()


def test_market_geography_persists_and_round_trips() -> None:
    market = resolve("06901")
    Session = make_session()
    with Session() as session:
        row = upsert_market(session, market)
        session.commit()
        restored = market_from_orm(row)

    assert restored == market


def test_checked_in_geography_database_is_healthy_and_substantial() -> None:
    index = USGeographyIndex(ROOT / "data" / "us_geography.sqlite3")
    metadata = index.metadata()

    assert metadata["dataset_version"] == "us-geography-2024.1"
    assert int(metadata["city_count"]) > 30_000
    assert int(metadata["zip_count"]) > 32_000
    with sqlite3.connect(index.path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
