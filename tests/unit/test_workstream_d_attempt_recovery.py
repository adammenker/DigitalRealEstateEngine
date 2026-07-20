from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import ApiCallORM, ProviderDailyUsageORM, ScanPlanCallORM, ScanRunORM
from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider, DataForSEOPlanError
from rank_rent.services.cache import normalize_request
from rank_rent.services.cost_controls import (
    mark_provider_call_submitted,
    reconcile_stale_api_call_attempts,
    reserve_provider_call,
    resolve_unknown_provider_call,
)
from rank_rent.services.qualification import DATAFORSEO_ADAPTER_VERSION
from rank_rent.services.scan_leases import ScanExecutionLease, ScanLeaseLost
from rank_rent.settings import Settings


def make_session() -> sessionmaker:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def settings() -> Settings:
    return Settings(
        data_mode="live",
        dataforseo_environment="sandbox",
        dataforseo_login="test",
        dataforseo_password="test",
        allow_live_api_calls=True,
    )


def prepare_attempt(session):
    now = datetime.now(UTC)
    scan = ScanRunORM(
        source="manual_async",
        status="running",
        worker_id="worker-a",
        lease_token="lease-a",
        heartbeat_at=now,
        lease_expires_at=now + timedelta(minutes=1),
        scan_profile="testing",
    )
    session.add(scan)
    session.flush()
    provider = DataForSEOLiveProvider(settings=settings(), session=session)
    provider.current_scan_run_id = scan.id
    provider.execution_lease = ScanExecutionLease(scan.id, "worker-a", "lease-a")
    path = "/v3/dataforseo_labs/google/keyword_suggestions/live"
    params = normalize_request({"tasks": [{"keyword": "drywall"}]})
    session.add(
        ScanPlanCallORM(
            scan_run_id=scan.id,
            planned_request_id="req-001",
            provider=provider.provider_name,
            endpoint=path,
            stage="keyword_discovery",
            request_parameters=params,
            cache_key=provider._cache_key(path, params),
            request_known=True,
            estimated_cost_usd=0.25,
        )
    )
    session.commit()
    api_call = provider._start_api_call(path, params)
    assert api_call is not None
    reservation = reserve_provider_call(
        session,
        settings=settings(),
        provider=provider.provider_name,
        environment=provider.api_environment,
        adapter_version=DATAFORSEO_ADAPTER_VERSION,
        endpoint=path,
        estimated_cost_usd=api_call.estimated_cost_usd,
        scan_profile="testing",
        cache_miss=True,
        api_call_id=api_call.id,
        execution_lease=provider.execution_lease,
        now=now,
    )
    return scan, provider, path, params, api_call, reservation, now


def test_stale_submitted_attempt_becomes_unknown_and_is_never_auto_retried() -> None:
    Session = make_session()
    with Session() as session:
        scan, provider, path, params, api_call, reservation, now = prepare_attempt(session)
        mark_provider_call_submitted(
            session,
            reservation,
            execution_lease=provider.execution_lease,
            now=now,
        )
        scan.lease_token = "replacement-lease"
        session.commit()

        assert (
            reconcile_stale_api_call_attempts(
                session,
                stale_before=now + timedelta(seconds=1),
                scan_run_id=scan.id,
                now=now + timedelta(seconds=2),
            )
            == 1
        )

        session.refresh(api_call)
        usage = session.query(ProviderDailyUsageORM).filter_by(endpoint="").one()
        assert api_call.status == "provider_outcome_unknown"
        assert api_call.provider_outcome == "unknown"
        assert api_call.reservation_state == "reconciled_unknown"
        assert usage.reserved_spend_usd == 0
        assert usage.unreconciled_spend_usd == pytest.approx(0.25)
        with pytest.raises(DataForSEOPlanError, match="no unused planned request"):
            provider._start_api_call(path, params)

        resolved = resolve_unknown_provider_call(
            session,
            api_call_id=api_call.id,
            outcome="not_billed",
            actual_cost_usd=0,
            resolution_note="Provider billing export contained no matching charge.",
            now=now + timedelta(minutes=1),
        )
        assert resolved.status == "provider_confirmed_not_billed"
        assert usage.unreconciled_spend_usd == 0


def test_stale_reserved_attempt_before_network_is_released_and_reusable() -> None:
    Session = make_session()
    with Session() as session:
        scan, provider, path, params, api_call, _, now = prepare_attempt(session)
        reconcile_stale_api_call_attempts(
            session,
            stale_before=now + timedelta(seconds=1),
            scan_run_id=scan.id,
            now=now + timedelta(seconds=2),
        )
        session.refresh(api_call)
        assert api_call.status == "failed_before_network"
        assert api_call.reservation_state == "released"
        retried = provider._start_api_call(path, params)
        assert retried is not None
        assert retried.id == api_call.id
        assert retried.status == "prepared"


def test_lost_lease_blocks_reservation_before_counters_or_network() -> None:
    Session = make_session()
    with Session() as session:
        scan, provider, path, _, api_call, reservation, now = prepare_attempt(session)
        # Release the setup reservation, then prepare a fresh safe-retry attempt.
        reconcile_stale_api_call_attempts(
            session,
            stale_before=now + timedelta(seconds=1),
            scan_run_id=scan.id,
            now=now + timedelta(seconds=2),
        )
        provider._start_api_call(path, normalize_request({"tasks": [{"keyword": "drywall"}]}))
        scan.lease_token = "worker-b-lease"
        session.commit()
        before = session.query(ProviderDailyUsageORM).filter_by(endpoint="").one().request_count
        with pytest.raises(ScanLeaseLost):
            reserve_provider_call(
                session,
                settings=settings(),
                provider=provider.provider_name,
                environment=provider.api_environment,
                adapter_version=DATAFORSEO_ADAPTER_VERSION,
                endpoint=path,
                estimated_cost_usd=api_call.estimated_cost_usd,
                scan_profile="testing",
                cache_miss=True,
                api_call_id=api_call.id,
                execution_lease=provider.execution_lease,
            )
        after = session.query(ProviderDailyUsageORM).filter_by(endpoint="").one().request_count
        assert after == before


@pytest.mark.asyncio
async def test_lease_loss_during_slow_http_call_records_unknown_and_discards_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Session = make_session()
    request_started = asyncio.Event()
    release_response = asyncio.Event()
    network_calls = 0

    async def slow_response(_request: httpx.Request) -> httpx.Response:
        nonlocal network_calls
        network_calls += 1
        request_started.set()
        await release_response.wait()
        return httpx.Response(
            200,
            json={
                "status_code": 20000,
                "tasks": [
                    {
                        "status_code": 20000,
                        "cost": 0,
                        "result": [{"items": []}],
                    }
                ],
            },
        )

    with Session() as session:
        now = datetime.now(UTC)
        scan = ScanRunORM(
            source="manual_async",
            status="running",
            worker_id="worker-a",
            lease_token="lease-a",
            heartbeat_at=now,
            lease_expires_at=now + timedelta(minutes=1),
            scan_profile="testing",
        )
        session.add(scan)
        session.flush()
        provider = DataForSEOLiveProvider(settings=settings(), session=session)
        provider.current_scan_run_id = scan.id
        provider.execution_lease = ScanExecutionLease(scan.id, "worker-a", "lease-a")
        path = "/v3/dataforseo_labs/google/keyword_suggestions/live"
        tasks = [{"keyword": "drywall"}]
        params = normalize_request({"tasks": tasks})
        session.add(
            ScanPlanCallORM(
                scan_run_id=scan.id,
                planned_request_id="req-slow",
                provider=provider.provider_name,
                endpoint=path,
                stage="keyword_discovery",
                request_parameters=params,
                cache_key=provider._cache_key(path, params),
                request_known=True,
                estimated_cost_usd=0.25,
            )
        )
        session.commit()

        def client() -> httpx.AsyncClient:
            return httpx.AsyncClient(
                base_url=provider.base_url,
                transport=httpx.MockTransport(slow_response),
            )

        monkeypatch.setattr(provider, "_client", client)
        request_task = asyncio.create_task(provider._post(path, tasks))
        await request_started.wait()
        scan.lease_token = "replacement-lease"
        session.commit()
        release_response.set()

        with pytest.raises(ScanLeaseLost):
            await request_task

        api_call = session.query(ApiCallORM).one()
        usage = session.query(ProviderDailyUsageORM).filter_by(endpoint="").one()
        assert network_calls == 1
        assert api_call.status == "provider_outcome_unknown"
        assert api_call.raw_api_response_id is None
        assert usage.reserved_spend_usd == 0
        assert usage.unreconciled_spend_usd == pytest.approx(0.25)
