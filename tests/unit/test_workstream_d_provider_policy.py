from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import ApiCallORM, ProviderDailyUsageORM, ScanPlanCallORM, ScanRunORM
from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider, DataForSEOPlanError
from rank_rent.services.cache import RawResponseCache, cache_key, normalize_request
from rank_rent.services.cost_controls import CircuitOpenError
from rank_rent.settings import Settings


def make_session() -> sessionmaker:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def settings() -> Settings:
    return Settings(
        data_mode="live",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        dataforseo_login="user",
        dataforseo_password="password",
        dataforseo_environment="sandbox",
    )


def add_plan(session, provider: DataForSEOLiveProvider, scan_id: int, path: str, params) -> None:
    session.add(
        ScanPlanCallORM(
            scan_run_id=scan_id,
            planned_request_id="req-001",
            provider=provider.provider_name,
            endpoint=path,
            stage="keyword_discovery",
            request_parameters=params,
            cache_key=cache_key(provider.provider_name, path, params, provider.api_version),
            estimated_cost_usd=0.01,
        )
    )
    session.commit()


@pytest.mark.asyncio
async def test_kill_switch_opens_transactional_circuit_before_http_client(monkeypatch) -> None:
    Session = make_session()
    path = "/v3/dataforseo_labs/google/keyword_suggestions/live"
    params = normalize_request({"tasks": [{"keyword": "drywall"}]})
    network_opened = False

    def client():
        nonlocal network_opened
        network_opened = True
        raise AssertionError("network must remain closed")

    with Session() as session:
        scan = ScanRunORM(source="manual_async", status="running", scan_profile="testing")
        session.add(scan)
        session.flush()
        provider = DataForSEOLiveProvider(settings=settings(), session=session)
        provider.current_scan_run_id = scan.id
        add_plan(session, provider, scan.id, path, params)
        provider.settings.paid_call_kill_switch = True
        monkeypatch.setattr(provider, "_client", client)

        with pytest.raises(CircuitOpenError, match="PAID_CALL_KILL_SWITCH"):
            await provider._post(path, [{"keyword": "drywall"}])

        assert network_opened is False
        assert session.query(ApiCallORM).one().status == "blocked"


@pytest.mark.asyncio
async def test_unplanned_call_is_counted_and_fails_before_network(monkeypatch) -> None:
    Session = make_session()
    network_opened = False

    def client():
        nonlocal network_opened
        network_opened = True
        raise AssertionError("network must remain closed")

    with Session() as session:
        scan = ScanRunORM(source="manual_async", status="running", scan_profile="testing")
        session.add(scan)
        session.flush()
        provider = DataForSEOLiveProvider(settings=settings(), session=session)
        provider.current_scan_run_id = scan.id
        monkeypatch.setattr(provider, "_client", client)

        with pytest.raises(DataForSEOPlanError, match="no unused planned request"):
            await provider._post("/unexpected", [{"keyword": "drywall"}])

        summary = session.query(ProviderDailyUsageORM).filter_by(endpoint="").one()
        assert summary.unexpected_call_count == 1
        assert network_opened is False


def test_failed_planned_request_reuses_the_same_idempotency_row() -> None:
    Session = make_session()
    path = "/endpoint"
    params = normalize_request({"tasks": [{"keyword": "drywall"}]})
    with Session() as session:
        scan = ScanRunORM(source="manual_async", status="running", scan_profile="testing")
        session.add(scan)
        session.flush()
        provider = DataForSEOLiveProvider(settings=settings(), session=session)
        provider.current_scan_run_id = scan.id
        add_plan(session, provider, scan.id, path, params)

        first = provider._start_api_call(path, params)
        assert first is not None
        provider._finish_api_call(first, status="failed", error=TimeoutError("transient"))
        second = provider._start_api_call(path, params)

        assert second is not None
        assert second.id == first.id
        assert second.status == "prepared"
        assert session.query(ApiCallORM).count() == 1


@pytest.mark.asyncio
async def test_repeated_cache_stage_execution_is_idempotent() -> None:
    Session = make_session()
    path = "/endpoint"
    params = normalize_request({"tasks": [{"keyword": "drywall"}]})
    with Session() as session:
        scan = ScanRunORM(source="manual_async", status="running", scan_profile="testing")
        session.add(scan)
        session.flush()
        provider = DataForSEOLiveProvider(settings=settings(), session=session)
        provider.current_scan_run_id = scan.id
        add_plan(session, provider, scan.id, path, params)
        RawResponseCache(session, provider.provider_name, provider.api_version).set(
            path,
            params,
            {"tasks": [{"status_code": 20000, "result": []}]},
        )
        provider._client = AsyncMock(side_effect=AssertionError("cache hits must not open HTTP"))  # type: ignore[method-assign]

        await provider._post(path, [{"keyword": "drywall"}])
        await provider._post(path, [{"keyword": "drywall"}])

        assert session.query(ApiCallORM).count() == 1
        assert session.query(ApiCallORM).one().status == "cache_hit"
