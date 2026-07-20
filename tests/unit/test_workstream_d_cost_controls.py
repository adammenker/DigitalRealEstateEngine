from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import BillingReconciliationORM, ProviderDailyUsageORM, ScanRunORM
from rank_rent.services.cost_controls import (
    CircuitOpenError,
    daily_usage,
    evaluate_alerts,
    finish_provider_call,
    record_unexpected_call,
    reserve_provider_call,
)
from rank_rent.services.qualification import (
    DATAFORSEO_ADAPTER_VERSION,
    REQUIRED_QUALIFICATION_CHECKS,
    record_qualification,
)
from rank_rent.settings import Settings


def make_session(path: Path | None = None) -> sessionmaker:
    url = f"sqlite:///{path}" if path else "sqlite:///:memory:"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_mode": "live",
        "allow_live_api_calls": True,
        "allow_production_dataforseo": True,
        "allow_full_scans": True,
        "paid_call_kill_switch": False,
        "dataforseo_login": "user",
        "dataforseo_password": "password",
        "dataforseo_environment": "production",
    }
    values.update(overrides)
    return Settings(**values)


def qualify_and_reconcile(session, now: datetime) -> None:
    record_qualification(
        session,
        provider="dataforseo-live",
        environment="production",
        adapter_version=DATAFORSEO_ADAPTER_VERSION,
        checks={name: True for name in REQUIRED_QUALIFICATION_CHECKS},
        ttl_hours=24,
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


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"allow_live_api_calls": False}, "ALLOW_LIVE_API_CALLS"),
        ({"allow_production_dataforseo": False}, "ALLOW_PRODUCTION_DATAFORSEO"),
        ({"paid_call_kill_switch": True}, "PAID_CALL_KILL_SWITCH"),
        ({"allow_full_scans": False}, "ALLOW_FULL_SCANS"),
    ],
)
def test_all_four_kill_switches_block_before_reservation(
    override: dict[str, object],
    message: str,
) -> None:
    Session = make_session()
    settings = production_settings(**override)
    with Session() as session:
        with pytest.raises(CircuitOpenError, match=message):
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
            )
        assert session.query(ProviderDailyUsageORM).count() == 0


def test_production_call_requires_current_matching_qualification_and_reconciliation() -> None:
    Session = make_session()
    now = datetime.now(UTC)
    with Session() as session:
        with pytest.raises(CircuitOpenError, match="qualification"):
            reserve_provider_call(
                session,
                settings=production_settings(),
                provider="dataforseo-live",
                environment="production",
                adapter_version=DATAFORSEO_ADAPTER_VERSION,
                endpoint="/paid",
                estimated_cost_usd=0.01,
                scan_profile="full",
                cache_miss=True,
                now=now,
            )
        record_qualification(
            session,
            provider="dataforseo-live",
            environment="production",
            adapter_version="old-adapter",
            checks={name: True for name in REQUIRED_QUALIFICATION_CHECKS},
            ttl_hours=24,
            now=now,
        )
        with pytest.raises(CircuitOpenError, match="adapter version"):
            reserve_provider_call(
                session,
                settings=production_settings(),
                provider="dataforseo-live",
                environment="production",
                adapter_version=DATAFORSEO_ADAPTER_VERSION,
                endpoint="/paid",
                estimated_cost_usd=0.01,
                scan_profile="full",
                cache_miss=True,
                now=now,
            )
        record_qualification(
            session,
            provider="dataforseo-live",
            environment="production",
            adapter_version=DATAFORSEO_ADAPTER_VERSION,
            checks={name: True for name in REQUIRED_QUALIFICATION_CHECKS},
            ttl_hours=1,
            now=now - timedelta(hours=2),
        )
        with pytest.raises(CircuitOpenError, match="stale"):
            reserve_provider_call(
                session,
                settings=production_settings(),
                provider="dataforseo-live",
                environment="production",
                adapter_version=DATAFORSEO_ADAPTER_VERSION,
                endpoint="/paid",
                estimated_cost_usd=0.01,
                scan_profile="full",
                cache_miss=True,
                now=now,
            )
        record_qualification(
            session,
            provider="dataforseo-live",
            environment="production",
            adapter_version=DATAFORSEO_ADAPTER_VERSION,
            checks={name: True for name in REQUIRED_QUALIFICATION_CHECKS},
            ttl_hours=24,
            now=now,
        )
        with pytest.raises(CircuitOpenError, match="billing"):
            reserve_provider_call(
                session,
                settings=production_settings(),
                provider="dataforseo-live",
                environment="production",
                adapter_version=DATAFORSEO_ADAPTER_VERSION,
                endpoint="/paid",
                estimated_cost_usd=0.01,
                scan_profile="full",
                cache_miss=True,
                now=now,
            )


def test_transactional_request_limit_allows_only_one_concurrent_reservation(tmp_path: Path) -> None:
    Session = make_session(tmp_path / "cost-limit.db")
    now = datetime.now(UTC)
    with Session() as session:
        qualify_and_reconcile(session, now)
    settings = production_settings(production_daily_request_limit=1)

    def reserve() -> str:
        with Session() as session:
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
    with Session() as session:
        summary = session.query(ProviderDailyUsageORM).filter_by(endpoint="").one()
        assert summary.request_count == 1
        assert summary.reserved_spend_usd == pytest.approx(0.01)


def test_durable_counters_endpoint_spend_and_synthetic_alerts() -> None:
    Session = make_session()
    settings = production_settings(testing_daily_spend_usd=1)
    now = datetime.now(UTC)
    with Session() as session:
        reservation = reserve_provider_call(
            session,
            settings=settings,
            provider="dataforseo-live",
            environment="production",
            adapter_version=DATAFORSEO_ADAPTER_VERSION,
            endpoint="/paid",
            estimated_cost_usd=0.6,
            scan_profile="testing",
            cache_miss=True,
            now=now,
        )
        finish_provider_call(
            session,
            reservation,
            actual_cost_usd=0.6,
            abnormal_cost=True,
        )
        record_unexpected_call(
            session,
            provider="dataforseo-live",
            scan_profile="testing",
            endpoint="/unexpected",
            now=now,
        )
        session.add(
            ScanRunORM(
                source="manual_async",
                status="quarantined",
                quarantined_at=now,
            )
        )
        session.commit()

        usage = daily_usage(session, provider="dataforseo-live", usage_date=now.date())
        alerts = evaluate_alerts(
            session,
            settings=settings,
            provider="dataforseo-live",
            usage_date=now.date(),
            now=now,
        )

    assert usage["testing_requests_today"] == 1
    assert usage["testing_spend_today"] == pytest.approx(0.6)
    assert usage["cache_misses"] == 1
    assert usage["unexpected_calls"] == 1
    assert usage["abnormal_cost_calls"] == 1
    assert usage["provider_endpoint_spend"] == {
        "testing:/paid": pytest.approx(0.6),
        "testing:/unexpected": 0.0,
    }
    assert "paid_testing_response" in alerts
    assert "daily_spend_50_percent" in alerts
    assert "unexpected_paid_call" in alerts
    assert "abnormal_endpoint_cost" in alerts
    assert "poison_job" in alerts
