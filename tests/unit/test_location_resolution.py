from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import MarketORM
from rank_rent.domain.models import LocationType, ServiceFamily
from rank_rent.planning import build_scan_plan
from rank_rent.runtime import DataMode
from rank_rent.services.locations import (
    LocationCandidate,
    LocationResolutionError,
    resolve_market_for_scan,
    search_locations,
)
from rank_rent.settings import Settings


def make_session() -> sessionmaker:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def settings() -> Settings:
    return Settings(project_root=Path(__file__).parents[2])


def test_city_state_input_resolves_without_exact_map_format() -> None:
    Session = make_session()
    with Session() as session:
        market = asyncio.run(
            resolve_market_for_scan(session, "London KY", "US", settings())
        )

    assert market.display_name == "London, KY, US"
    assert market.country_code == "US"
    assert market.state == "KY"
    assert market.cities == ["London"]
    assert market.provider_location_name == "London,Kentucky,United States"
    assert market.resolution_metadata["dataforseo_mapping_status"] == "inferred_provider_name"


def test_ambiguous_city_without_state_is_rejected() -> None:
    Session = make_session()
    with Session() as session:
        with pytest.raises(LocationResolutionError, match="ambiguous"):
            asyncio.run(resolve_market_for_scan(session, "London", "US", settings()))


def test_stale_cross_country_database_market_is_not_reused() -> None:
    Session = make_session()
    with Session() as session:
        session.add(
            MarketORM(
                slug="london",
                display_name="London",
                country_code="US",
                provider_location_code="1002316",
                provider_location_name="London Borough of Lambeth,England,United Kingdom",
                resolution_metadata={
                    "matched_location": "London Borough of Lambeth,England,United Kingdom"
                },
            )
        )
        session.commit()

        with pytest.raises(LocationResolutionError, match="ambiguous"):
            asyncio.run(resolve_market_for_scan(session, "London", "US", settings()))


def test_selected_location_is_used_as_canonical_market() -> None:
    candidate = LocationCandidate(
        id="pelias-london-ky",
        label="London, KY, US",
        type=LocationType.city,
        country="US",
        state="KY",
        city="London",
        latitude=37.129,
        longitude=-84.083,
        source="pelias",
        confidence=0.98,
        match_reason="pelias_locality",
    )
    Session = make_session()
    with Session() as session:
        market = asyncio.run(
            resolve_market_for_scan(session, "London", "US", settings(), candidate)
        )

    assert market.display_name == "London, KY, US"
    assert market.latitude == 37.129
    assert market.resolution_metadata["selected_location_source"] == "pelias"
    assert market.provider_location_name == "London,Kentucky,United States"


def test_seeded_markets_show_up_in_location_search() -> None:
    Session = make_session()
    with Session() as session:
        results = asyncio.run(search_locations(session, "Stamford", "US", settings()))

    assert any(result.label == "Stamford, CT" for result in results)


def test_ambiguous_city_search_returns_clickable_gazetteer_choices() -> None:
    Session = make_session()
    with Session() as session:
        results = asyncio.run(search_locations(session, "London", "US", settings()))

    labels = {result.label for result in results}
    assert {"London, KY, US", "London, OH, US"} <= labels


def test_inferred_provider_location_name_removes_location_lookup_from_plan() -> None:
    Session = make_session()
    service_settings = Settings(
        project_root=Path(__file__).parents[2],
        data_mode="live",
        allow_live_api_calls=True,
        dataforseo_login="user",
        dataforseo_password="password",
        dataforseo_environment="production",
    )
    with Session() as session:
        market = asyncio.run(resolve_market_for_scan(session, "London KY", "US", service_settings))
        plan = build_scan_plan(
            service_settings,
            DataMode.live,
            service=ServiceFamily(
                id="water-heater-repair",
                display_name="Water Heater Repair",
                seed_queries=["water heater repair"],
            ),
            market=market,
            session=session,
        )

    assert "location_resolution" not in [call.stage for call in plan.planned_calls]
    serp_call = next(call for call in plan.planned_calls if call.stage == "serp")
    assert serp_call.request_parameters["tasks"][0]["location_name"] == "London,Kentucky,United States"
