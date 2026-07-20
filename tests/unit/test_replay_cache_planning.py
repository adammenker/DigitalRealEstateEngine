from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import (
    ApiCallORM,
    RawApiResponseORM,
    ScanPlanCallORM,
    ScanRunORM,
)
from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.integrations.dataforseo.live import (
    DataForSEOLiveProvider,
    DataForSEOPlanError,
)
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
from rank_rent.services.locations import market_from_geography_record
from rank_rent.services.us_geography import USGeographyIndex
from rank_rent.settings import Settings


class SlowDataForSEOClient:
    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        self.started = started
        self.release = release

    async def __aenter__(self) -> SlowDataForSEOClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, path: str, json: list[dict[str, Any]]) -> httpx.Response:
        self.started.set()
        await self.release.wait()
        return httpx.Response(
            200,
            json={
                "status_code": 20000,
                "tasks": [
                    {
                        "id": "slow-task",
                        "status_code": 20000,
                        "cost": 0,
                        "result": [],
                    }
                ],
            },
        )

    async def get(self, path: str) -> httpx.Response:
        self.started.set()
        await self.release.wait()
        return httpx.Response(
            200,
            json={
                "status_code": 20000,
                "tasks": [{"id": "slow-task", "status_code": 20000, "cost": 0, "result": []}],
            },
        )


def make_session() -> sessionmaker:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def canonical_market(query: str = "St. Louis MO") -> Market:
    index = USGeographyIndex(Path(__file__).parents[2] / "data" / "us_geography.sqlite3")
    return market_from_geography_record(index.search(query, limit=1)[0].record)


def make_file_session(db_path: str) -> sessionmaker:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 0.1},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def add_planned_call(
    session: Any,
    provider: DataForSEOLiveProvider,
    scan_id: int,
    *,
    planned_request_id: str,
    path: str,
    params: dict[str, Any],
    request_known: bool = True,
    estimated_cost_usd: float = 0,
) -> None:
    session.add(
        ScanPlanCallORM(
            scan_run_id=scan_id,
            planned_request_id=planned_request_id,
            provider=provider.provider_name,
            endpoint=path,
            stage="test",
            request_parameters=params,
            cache_key=provider._cache_key(path, params),
            request_known=request_known,
            estimated_cost_usd=estimated_cost_usd,
            required=True,
        )
    )
    session.commit()


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
        allow_production_dataforseo=True,
        allow_full_scans=True,
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
async def test_live_call_ledger_does_not_hold_sqlite_write_lock_during_slow_http(
    tmp_path, monkeypatch
) -> None:
    Session = make_file_session(str(tmp_path / "sqlite_lock.db"))
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        allow_full_scans=True,
        dataforseo_login="user",
        dataforseo_password="password",
        dataforseo_environment="sandbox",
    )
    started = asyncio.Event()
    release = asyncio.Event()
    with Session() as session:
        scan = ScanRunORM(source="manual", status="running")
        session.add(scan)
        session.commit()
        provider = DataForSEOLiveProvider(settings=settings, session=session)
        provider.current_scan_run_id = scan.id
        path = "/v3/dataforseo_labs/google/keyword_suggestions/live"
        params = normalize_request({"tasks": [{"keyword": "drywall"}]})
        add_planned_call(
            session,
            provider,
            scan.id,
            planned_request_id="req-001",
            path=path,
            params=params,
        )
        monkeypatch.setattr(
            provider,
            "_client",
            lambda: SlowDataForSEOClient(started, release),
        )

        request = asyncio.create_task(
            provider._post(
                path,
                [{"keyword": "drywall"}],
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)

        with Session() as other_session:
            running_call = other_session.query(ApiCallORM).one()
            assert running_call.status == "running"
            same_scan = other_session.get(ScanRunORM, scan.id)
            assert same_scan is not None
            same_scan.heartbeat_at = datetime.now(UTC)
            other_session.commit()

        release.set()
        payload = await asyncio.wait_for(request, timeout=1)
        assert payload["tasks"][0]["id"] == "slow-task"
        completed_call = session.query(ApiCallORM).one()

    assert completed_call.status == "completed"


def test_full_scan_plan_matches_executor_keyword_seed_requests() -> None:
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = canonical_market()
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        allow_full_scans=True,
        dataforseo_login="user",
        dataforseo_password="password",
        live_scan_depth="full",
        dataforseo_environment="production",
    )

    plan = build_scan_plan(settings, DataMode.live, service, market)
    keyword_calls = [
        call
        for call in plan.planned_calls
        if call.endpoint == "/v3/dataforseo_labs/google/keyword_suggestions/live"
    ]

    assert plan.blocked is False
    assert len(plan.planned_calls) == 13
    assert len(keyword_calls) == 3
    assert [call.request_parameters["tasks"][0]["keyword"] for call in keyword_calls] == [
        "drywall",
        "drywall repair",
        "drywall replacement",
    ]
    assert len({call.planned_request_id for call in plan.planned_calls}) == len(plan.planned_calls)


def test_full_scan_plan_with_offline_geography_fits_default_request_limit() -> None:
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = canonical_market()
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        allow_full_scans=True,
        dataforseo_login="user",
        dataforseo_password="password",
        live_scan_depth="full",
        dataforseo_environment="production",
    )

    plan = build_scan_plan(settings, DataMode.live, service, market)

    assert plan.blocked is False
    assert len(plan.planned_calls) == 13
    assert "location_resolution" not in {call.stage for call in plan.planned_calls}
    assert plan.maximum_request_count == 15


@pytest.mark.asyncio
async def test_dataforseo_cache_hit_writes_api_call_ledger_row() -> None:
    Session = make_session()
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        dataforseo_login="user",
        dataforseo_password="password",
        dataforseo_environment="sandbox",
    )
    params = normalize_request({"tasks": [{"keyword": "drywall"}]})
    with Session() as session:
        scan = ScanRunORM(source="manual", status="running")
        session.add(scan)
        session.flush()
        provider = DataForSEOLiveProvider(settings=settings, session=session)
        provider.current_scan_run_id = scan.id
        add_planned_call(
            session,
            provider,
            scan.id,
            planned_request_id="req-001",
            path="/endpoint",
            params=params,
        )
        cache = RawResponseCache(session, "dataforseo-sandbox", "v3")
        cache.set(
            "/endpoint",
            params,
            {"tasks": [{"status_code": 20000, "result": []}]},
            cost_usd=0,
            source_scan_run_id=scan.id,
        )

        assert await provider._post("/endpoint", [{"keyword": "drywall"}]) == {
            "tasks": [{"status_code": 20000, "result": []}]
        }
        row = session.query(ApiCallORM).one()

    assert row.planned_request_id == "req-001"
    assert row.status == "cache_hit"
    assert row.cache_hit is True
    assert row.actual_cost_usd == 0


@pytest.mark.asyncio
async def test_unplanned_production_scan_call_fails_before_network(
    monkeypatch,
) -> None:
    Session = make_session()
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        dataforseo_login="user",
        dataforseo_password="password",
        dataforseo_environment="production",
    )
    path = "/v3/dataforseo_labs/google/keyword_suggestions/live"
    planned_params = normalize_request({"tasks": [{"keyword": "drywall"}]})
    network_opened = False

    def unexpected_client() -> None:
        nonlocal network_opened
        network_opened = True
        raise AssertionError("The HTTP client must not open for an unplanned request.")

    with Session() as session:
        provider = DataForSEOLiveProvider(settings=settings, session=session)
        scan = ScanRunORM(source="manual", status="running")
        session.add(scan)
        session.flush()
        provider.current_scan_run_id = scan.id
        add_planned_call(
            session,
            provider,
            scan.id,
            planned_request_id="req-001",
            path=path,
            params=planned_params,
        )
        monkeypatch.setattr(provider, "_client", unexpected_client)

        with pytest.raises(DataForSEOPlanError, match="no unused planned request"):
            await provider._post(path, [{"keyword": "drywall repair"}])

        assert network_opened is False
        assert session.query(ApiCallORM).count() == 0


def test_identical_requests_consume_distinct_planned_request_ids() -> None:
    Session = make_session()
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        dataforseo_login="user",
        dataforseo_password="password",
        dataforseo_environment="production",
    )
    path = "/v3/dataforseo_labs/google/keyword_suggestions/live"
    params = normalize_request({"tasks": [{"keyword": "drywall"}]})
    with Session() as session:
        provider = DataForSEOLiveProvider(settings=settings, session=session)
        scan = ScanRunORM(source="manual", status="running")
        session.add(scan)
        session.flush()
        provider.current_scan_run_id = scan.id
        for planned_request_id in ("req-001", "req-002"):
            add_planned_call(
                session,
                provider,
                scan.id,
                planned_request_id=planned_request_id,
                path=path,
                params=params,
                estimated_cost_usd=0.012,
            )

        first = provider._start_api_call(path, params)
        second = provider._start_api_call(path, params)

        assert first is not None
        assert second is not None
        assert first.planned_request_id == "req-001"
        assert second.planned_request_id == "req-002"
        with pytest.raises(DataForSEOPlanError, match="no unused planned request"):
            provider._start_api_call(path, params)


def test_unique_reservation_constraint_rejects_a_matching_race(
    monkeypatch,
) -> None:
    Session = make_session()
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        dataforseo_login="user",
        dataforseo_password="password",
        dataforseo_environment="production",
    )
    path = "/v3/dataforseo_labs/google/keyword_suggestions/live"
    params = normalize_request({"tasks": [{"keyword": "drywall"}]})
    with Session() as session:
        provider = DataForSEOLiveProvider(settings=settings, session=session)
        scan = ScanRunORM(source="manual", status="running")
        session.add(scan)
        session.flush()
        provider.current_scan_run_id = scan.id
        add_planned_call(
            session,
            provider,
            scan.id,
            planned_request_id="req-001",
            path=path,
            params=params,
        )
        stale_match = provider._planned_call(path, params)
        provider._start_api_call(path, params)
        monkeypatch.setattr(
            provider,
            "_planned_call",
            lambda *_args, **_kwargs: stale_match,
        )

        with pytest.raises(DataForSEOPlanError, match="already consumed"):
            provider._start_api_call(path, params)

        assert session.query(ApiCallORM).count() == 1


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
    market = canonical_market()
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        dataforseo_login="user",
        dataforseo_password="password",
        live_scan_depth="testing",
        max_scan_cost_usd=0.001,
        dataforseo_environment="production",
    )
    plan = build_scan_plan(settings, DataMode.live, service, market)
    assert len(plan.planned_calls) == 4
    assert plan.planned_calls[0].stage == "keyword_discovery"
    assert "location_coordinate" in plan.planned_calls[-1].request_parameters["tasks"][0]
    assert plan.blocked is True
    assert plan.block_reason is not None


def test_sandbox_scan_plan_uses_free_sandbox_provider() -> None:
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = canonical_market()
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
    provider_task = next(
        call for call in plan.planned_calls if call.stage == "provider_discovery"
    ).request_parameters["tasks"][0]
    assert "location_coordinate" not in provider_task


def test_scan_plan_marks_exact_cached_calls() -> None:
    Session = make_session()
    service = ServiceFamily(id="drywall", display_name="Drywall", seed_queries=["drywall"])
    market = canonical_market()
    settings = Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        dataforseo_login="user",
        dataforseo_password="password",
        live_scan_depth="testing",
        max_scan_cost_usd=10,
        dataforseo_environment="production",
    )
    with Session() as session:
        first_plan = build_scan_plan(settings, DataMode.live, service, market)
        keyword_call = next(
            call for call in first_plan.planned_calls if call.stage == "keyword_discovery"
        )
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

    cached_keyword_call = next(
        call for call in cached_plan.planned_calls if call.stage == "keyword_discovery"
    )
    assert cached_keyword_call.cache_hit is True
    assert cached_plan.cached_cost_usd == Decimal("0.012")
    assert cached_plan.estimated_uncached_cost_usd == Decimal("0.024")
