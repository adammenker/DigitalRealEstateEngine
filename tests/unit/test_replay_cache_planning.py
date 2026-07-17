from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import RawApiResponseORM, ScanRunORM
from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider
from rank_rent.integrations.dataforseo.replay import DataForSEOReplayProvider
from rank_rent.planning import build_scan_plan
from rank_rent.replay import (
    BundleReplayTransport,
    DatabaseReplayTransport,
    ReplayIntegrityError,
    ReplayMissError,
    StoredApiResponse,
    export_responses_for_scan,
    load_response_bundle,
)
from rank_rent.runtime import DataMode
from rank_rent.services.cache import (
    RawResponseCache,
    cache_key,
    checksum_payload,
    normalize_request,
)
from rank_rent.settings import Settings


def make_session() -> sessionmaker:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_cache_key_is_order_independent() -> None:
    left = cache_key("provider", "/endpoint", {"b": 2, "a": 1}, "v1")
    right = cache_key("provider", "/endpoint", {"a": 1, "b": 2}, "v1")
    assert left == right


def test_raw_response_cache_hit_and_miss() -> None:
    Session = make_session()
    with Session() as session:
        cache = RawResponseCache(session, "dataforseo-live", "v3")
        assert cache.get("/endpoint", {"keyword": "drywall"}) is None
        cache.set("/endpoint", {"keyword": "drywall"}, {"tasks": []}, cost_usd=0.01)
        assert cache.get("/endpoint", {"keyword": "drywall"}) == {"tasks": []}


def test_raw_response_cache_sanitizes_and_rejects_expired_rows() -> None:
    Session = make_session()
    with Session() as session:
        cache = RawResponseCache(session, "dataforseo-live", "v3")
        cache.set(
            "/endpoint",
            {"keyword": "drywall"},
            {"tasks": [], "password": "secret", "nested": {"api_key": "secret"}},
            cost_usd=0.01,
        )
        row = cache.get_row("/endpoint", {"keyword": "drywall"})
        assert row is not None
        assert row.response_json["password"] == "<redacted>"
        assert row.response_json["nested"]["api_key"] == "<redacted>"
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

        assert cache.get("/endpoint", {"keyword": "drywall"}) is None


def test_sandbox_provider_stores_free_sandbox_cache_rows() -> None:
    Session = make_session()
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        dataforseo_login="user",
        dataforseo_password="password",
        dataforseo_environment="sandbox",
    )
    with Session() as session:
        provider = DataForSEOLiveProvider(settings=settings, session=session)
        provider._cache_set(
            "/v3/test",
            {"tasks": [{"keyword": "drywall"}]},
            {"tasks": [{"id": "task-id", "cost": 9.99, "result": []}], "request_id": "request-id"},
            status_code=200,
        )
        row = session.query(RawApiResponseORM).one()

    assert row.provider == "dataforseo-sandbox"
    assert row.cost_usd == 0
    assert row.provider_task_id == "task-id"
    assert row.provider_request_id == "request-id"


@pytest.mark.asyncio
async def test_replay_provider_reads_stored_response_without_network() -> None:
    Session = make_session()
    with Session() as session:
        params = normalize_request({"tasks": [{"keyword": "drywall"}]})
        session.add(
            RawApiResponseORM(
                cache_key=cache_key("dataforseo-live", "/endpoint", params, "v3"),
                provider="dataforseo-live",
                endpoint="/endpoint",
                parameters=params,
                api_version="v3",
                response_json={"tasks": [{"status_code": 20000, "result": []}]},
                request_time=datetime.now(UTC),
                response_time=datetime.now(UTC),
                cost_usd=0.01,
            )
        )
        session.commit()
        provider = DataForSEOReplayProvider(DatabaseReplayTransport(session))
        assert await provider._post("/endpoint", [{"keyword": "drywall"}]) == {
            "tasks": [{"status_code": 20000, "result": []}]
        }
        with pytest.raises(ReplayMissError):
            await provider._get("/missing")


@pytest.mark.asyncio
async def test_bundle_replay_uses_requested_api_version() -> None:
    params = normalize_request({"tasks": [{"keyword": "drywall"}]})
    stored = StoredApiResponse(
        provider="dataforseo-live",
        endpoint="/endpoint",
        api_version="v4",
        normalized_request=params,
        response_body={"tasks": [{"status_code": 20000, "result": []}]},
        provider_cost_usd=Decimal("0.01"),
        requested_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        checksum=checksum_payload({"tasks": [{"status_code": 20000, "result": []}]}),
    )
    transport = BundleReplayTransport([stored])

    assert await transport.get_response("dataforseo-live", "/endpoint", params, "v4") == stored
    with pytest.raises(ReplayMissError):
        await transport.get_response("dataforseo-live", "/endpoint", params, "v3")


def test_export_responses_for_scan_limits_to_scan_window(tmp_path) -> None:
    Session = make_session()
    with Session() as session:
        scan = ScanRunORM(
            source="manual",
            status="completed",
            started_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            completed_at=datetime(2026, 1, 1, 12, 5, tzinfo=UTC),
        )
        session.add(scan)
        inside_params = normalize_request({"tasks": [{"keyword": "inside"}]})
        outside_params = normalize_request({"tasks": [{"keyword": "outside"}]})
        session.add_all(
            [
                RawApiResponseORM(
                    cache_key=cache_key("dataforseo-live", "/inside", inside_params, "v3"),
                    provider="dataforseo-live",
                    endpoint="/inside",
                    parameters=inside_params,
                    api_version="v3",
                    response_json={"tasks": [{"result": []}]},
                    request_time=datetime(2026, 1, 1, 12, 1, tzinfo=UTC),
                    response_time=datetime(2026, 1, 1, 12, 1, tzinfo=UTC),
                ),
                RawApiResponseORM(
                    cache_key=cache_key("dataforseo-live", "/outside", outside_params, "v3"),
                    provider="dataforseo-live",
                    endpoint="/outside",
                    parameters=outside_params,
                    api_version="v3",
                    response_json={"tasks": [{"result": []}]},
                    request_time=datetime(2026, 1, 1, 11, 59, tzinfo=UTC),
                    response_time=datetime(2026, 1, 1, 11, 59, tzinfo=UTC),
                ),
            ]
        )
        session.commit()

        output = tmp_path / "responses.json"
        export_responses_for_scan(session, str(output), scan_run_id=scan.id)

    payload = json.loads(output.read_text())
    assert [item["endpoint"] for item in payload["responses"]] == ["/inside"]
    assert payload["responses"][0]["source_scan_run_id"] == scan.id


@pytest.mark.asyncio
async def test_load_response_bundle_returns_replay_transport(tmp_path) -> None:
    params = normalize_request({"tasks": [{"keyword": "drywall"}]})
    stored = StoredApiResponse(
        provider="dataforseo-live",
        endpoint="/endpoint",
        api_version="v3",
        normalized_request=params,
        response_body={"tasks": [{"status_code": 20000, "result": []}]},
        provider_cost_usd=Decimal("0.01"),
        requested_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        checksum=checksum_payload({"tasks": [{"status_code": 20000, "result": []}]}),
    )
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"responses": [stored.model_dump(mode="json")]}))

    transport = load_response_bundle(str(bundle))

    response = await transport.get_response("dataforseo-live", "/endpoint", params, "v3")
    assert response.response_body == stored.response_body


def test_corrupted_response_bundle_is_rejected(tmp_path) -> None:
    params = normalize_request({"tasks": [{"keyword": "drywall"}]})
    stored = StoredApiResponse(
        provider="dataforseo-live",
        endpoint="/endpoint",
        api_version="v3",
        normalized_request=params,
        response_body={"tasks": [{"status_code": 20000, "result": []}]},
        provider_cost_usd=Decimal("0.01"),
        requested_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        checksum="not-the-real-checksum",
    )
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"responses": [stored.model_dump(mode="json")]}))

    with pytest.raises(ReplayIntegrityError):
        load_response_bundle(str(bundle))


def test_testing_scan_plan_is_low_cost_and_blocks_over_budget() -> None:
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = Market(id="st_louis", display_name="St. Louis, MO")
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        dataforseo_login="user",
        dataforseo_password="password",
        live_scan_depth="testing",
        max_scan_cost_usd=0.001,
        dataforseo_environment="production",
    )
    plan = build_scan_plan(settings, DataMode.live, service, market)
    assert len(plan.planned_calls) == 5
    assert plan.planned_calls[0].stage == "location_resolution"
    assert plan.planned_calls[0].estimated_cost_usd == 0
    assert "location_coordinate" in plan.planned_calls[-1].request_parameters["tasks"][0]
    assert plan.blocked is True
    assert plan.block_reason is not None


def test_sandbox_scan_plan_uses_free_sandbox_provider() -> None:
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = Market(id="st_louis", display_name="St. Louis, MO")
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        dataforseo_login="user",
        dataforseo_password="password",
        live_scan_depth="testing",
        max_scan_cost_usd=0,
        dataforseo_environment="sandbox",
    )
    plan = build_scan_plan(settings, DataMode.live, service, market)

    assert plan.blocked is False
    assert plan.confirmation_required is False
    assert plan.estimated_uncached_cost_usd == Decimal("0")
    assert {call.provider for call in plan.planned_calls} == {"dataforseo-sandbox"}


def test_scan_plan_marks_exact_cached_calls() -> None:
    Session = make_session()
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = Market(id="st_louis", display_name="St. Louis, MO")
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        dataforseo_login="user",
        dataforseo_password="password",
        live_scan_depth="testing",
        max_scan_cost_usd=10,
        dataforseo_environment="production",
    )
    with Session() as session:
        first_plan = build_scan_plan(settings, DataMode.live, service, market)
        keyword_call = first_plan.planned_calls[1]
        session.add(
            RawApiResponseORM(
                cache_key=keyword_call.cache_key,
                provider=keyword_call.provider,
                endpoint=keyword_call.endpoint,
                parameters=keyword_call.request_parameters,
                api_version="v3",
                response_json={"tasks": [{"status_code": 20000, "result": []}]},
                request_time=datetime.now(UTC),
                response_time=datetime.now(UTC),
                cost_usd=0.012,
            )
        )
        session.commit()

        cached_plan = build_scan_plan(settings, DataMode.live, service, market, session=session)

    assert cached_plan.planned_calls[1].cache_hit is True
    assert cached_plan.cached_cost_usd == Decimal("0.012")
    assert cached_plan.estimated_uncached_cost_usd == Decimal("0.024")
