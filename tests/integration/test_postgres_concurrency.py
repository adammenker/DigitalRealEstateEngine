from __future__ import annotations

import os
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import (
    ApiCallORM,
    BillingReconciliationORM,
    ProviderDailyUsageORM,
    ScanPlanCallORM,
    ScanRunORM,
)
from rank_rent.integrations.dataforseo.live import DataForSEOLiveProvider, DataForSEOPlanError
from rank_rent.services.cache import cache_key, normalize_request
from rank_rent.services.cost_controls import CircuitOpenError, reserve_provider_call
from rank_rent.services.qualification import (
    DATAFORSEO_ADAPTER_VERSION,
    REQUIRED_QUALIFICATION_CHECKS,
    record_executed_qualification,
)
from rank_rent.services.scan_worker import claim_next_scan
from rank_rent.settings import Settings

POSTGRESQL_URL = os.getenv("TEST_POSTGRESQL_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRESQL_URL,
    reason="TEST_POSTGRESQL_URL is required for PostgreSQL integration coverage.",
)


@pytest.fixture(scope="module")
def postgres_session() -> Generator[sessionmaker, None, None]:
    if not POSTGRESQL_URL:
        pytest.skip("TEST_POSTGRESQL_URL is not configured.")
    engine = make_engine(
        POSTGRESQL_URL,
        Settings(database_url=POSTGRESQL_URL),
    )
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_postgres_workers_claim_one_lease_atomically(postgres_session: sessionmaker) -> None:
    with postgres_session() as session:
        scan = ScanRunORM(source="manual_async", status="queued")
        session.add(scan)
        session.commit()
        scan_id = scan.id

    def claim(worker_id: str):
        with postgres_session() as session:
            return claim_next_scan(session, worker_id=worker_id)

    with ThreadPoolExecutor(max_workers=4) as pool:
        leases = list(pool.map(claim, ("worker-a", "worker-b", "worker-c", "worker-d")))

    claimed = [lease for lease in leases if lease is not None]
    assert len(claimed) == 1
    assert claimed[0].scan_id == scan_id
    with postgres_session() as session:
        stored = session.get(ScanRunORM, scan_id)
        assert stored is not None
        assert stored.lease_token == claimed[0].lease_token


def test_postgres_daily_limit_is_transactional(postgres_session: sessionmaker) -> None:
    now = datetime.now(UTC)
    settings = Settings(
        data_mode="live",
        dataforseo_environment="production",
        dataforseo_login="test",
        dataforseo_password="test",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        allow_full_scans=True,
        production_daily_request_limit=1,
    )
    with postgres_session() as session:
        record_executed_qualification(
            session,
            provider="dataforseo-live",
            environment="production",
            adapter_version=DATAFORSEO_ADAPTER_VERSION,
            checks={
                name: {"passed": True, "evidence": {"source": "postgres-test"}}
                for name in REQUIRED_QUALIFICATION_CHECKS
            },
            ttl_hours=24,
            executed_by="test-suite",
            now=now,
        )
        session.add(
            BillingReconciliationORM(
                provider="dataforseo-live",
                environment="production",
                period_start=now.date(),
                period_end=now.date(),
                reconciled_at=now,
                status="clean",
            )
        )
        session.commit()

    def reserve() -> str:
        with postgres_session() as session:
            try:
                reserve_provider_call(
                    session,
                    settings=settings,
                    provider="dataforseo-live",
                    environment="production",
                    adapter_version=DATAFORSEO_ADAPTER_VERSION,
                    endpoint="/paid",
                    estimated_cost_usd=0.01,
                    scan_profile="full",
                    cache_miss=True,
                    now=now,
                )
            except CircuitOpenError:
                return "blocked"
            return "reserved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))

    assert sorted(results) == ["blocked", "reserved"]
    with postgres_session() as session:
        summary = session.query(ProviderDailyUsageORM).filter_by(endpoint="").one()
        assert summary.request_count == 1
        assert summary.reserved_spend_usd == pytest.approx(0.01)


def test_postgres_planned_call_is_consumed_once(postgres_session: sessionmaker) -> None:
    settings = Settings(
        data_mode="live",
        dataforseo_environment="production",
        dataforseo_login="test",
        dataforseo_password="test",
        allow_live_api_calls=True,
        allow_production_dataforseo=True,
        allow_full_scans=True,
    )
    path = "/v3/dataforseo_labs/google/keyword_suggestions/live"
    params = normalize_request({"tasks": [{"keyword": "drywall"}]})
    provider_name = "dataforseo-live"
    with postgres_session() as session:
        scan = ScanRunORM(source="manual_async", status="running", scan_profile="full")
        session.add(scan)
        session.flush()
        scan_id = scan.id
        session.add(
            ScanPlanCallORM(
                scan_run_id=scan_id,
                planned_request_id="postgres-race-001",
                provider=provider_name,
                endpoint=path,
                stage="keyword_discovery",
                request_parameters=params,
                cache_key=cache_key(provider_name, path, params, "v3"),
                estimated_cost_usd=0.01,
            )
        )
        session.commit()

    def consume() -> str:
        with postgres_session() as session:
            provider = DataForSEOLiveProvider(settings=settings, session=session)
            provider.current_scan_run_id = scan_id
            provider.scan_profile_override = "full"
            try:
                provider._start_api_call(path, params)
            except DataForSEOPlanError:
                return "blocked"
            return "reserved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))

    assert sorted(results) == ["blocked", "reserved"]
    with postgres_session() as session:
        rows = session.query(ApiCallORM).filter_by(scan_run_id=scan_id).all()
        assert len(rows) == 1
        assert rows[0].planned_request_id == "postgres-race-001"
