from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import ApiCallORM, BillingReconciliationORM
from rank_rent.services.billing import reconcile_billing_csv
from rank_rent.services.qualification import (
    DATAFORSEO_ADAPTER_VERSION,
    REQUIRED_QUALIFICATION_CHECKS,
    current_qualification,
    record_qualification,
)


def make_session() -> sessionmaker:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_billing_csv_reconciliation_reports_a_clean_exact_match(tmp_path: Path) -> None:
    Session = make_session()
    now = datetime.now(UTC)
    csv_path = tmp_path / "billing.csv"
    csv_path.write_text(
        "provider_request_id,provider_task_id,endpoint,cost_usd,billed_at\n"
        f"request-1,task-1,/v3/serp,0.25,{now.isoformat()}\n"
    )
    with Session() as session:
        session.add(
            ApiCallORM(
                provider="dataforseo-live",
                endpoint="/v3/serp",
                stage="serp",
                cache_key="key",
                status="completed",
                actual_cost_usd=0.25,
                provider_request_id="request-1",
                provider_task_id="task-1",
                completed_at=now,
            )
        )
        session.commit()

        report = reconcile_billing_csv(
            session,
            csv_path,
            provider="dataforseo-live",
            environment="production",
            tolerance_usd=0.01,
            now=now,
        )

        stored = session.query(BillingReconciliationORM).one()
    assert report == {
        "status": "clean",
        "internal_call_count": 1,
        "provider_call_count": 1,
        "internal_cost": 0.25,
        "provider_cost": 0.25,
        "unmatched_provider_charges": [],
        "unmatched_internal_calls": [],
        "difference": 0.0,
    }
    assert stored.source_filename == "billing.csv"


def test_billing_csv_reconciliation_surfaces_unmatched_charges(tmp_path: Path) -> None:
    Session = make_session()
    now = datetime.now(UTC)
    csv_path = tmp_path / "billing.csv"
    csv_path.write_text(
        "provider_request_id,provider_task_id,endpoint,cost_usd,billed_at\n"
        f"unknown,unknown-task,/v3/serp,0.50,{now.isoformat()}\n"
    )
    with Session() as session:
        report = reconcile_billing_csv(
            session,
            csv_path,
            provider="dataforseo-live",
            environment="production",
            tolerance_usd=0.01,
            now=now,
        )
    assert report["status"] == "mismatch"
    assert report["provider_call_count"] == 1
    assert len(report["unmatched_provider_charges"]) == 1
    assert report["difference"] == pytest.approx(0.5)


def test_qualification_requires_every_check_and_expires() -> None:
    Session = make_session()
    now = datetime.now(UTC)
    with Session() as session:
        failed = record_qualification(
            session,
            provider="dataforseo-live",
            environment="production",
            adapter_version=DATAFORSEO_ADAPTER_VERSION,
            checks={"account_access": True},
            ttl_hours=24,
            now=now,
        )
        assert failed.status == "failed"
        assert (
            current_qualification(
                session,
                provider="dataforseo-live",
                environment="production",
                adapter_version=DATAFORSEO_ADAPTER_VERSION,
                now=now,
            )
            is None
        )
        passed = record_qualification(
            session,
            provider="dataforseo-live",
            environment="production",
            adapter_version=DATAFORSEO_ADAPTER_VERSION,
            checks={name: {"passed": True} for name in REQUIRED_QUALIFICATION_CHECKS},
            ttl_hours=1,
            now=now + timedelta(minutes=1),
        )
        assert passed.status == "passed"
        assert (
            current_qualification(
                session,
                provider="dataforseo-live",
                environment="production",
                adapter_version=DATAFORSEO_ADAPTER_VERSION,
                now=now + timedelta(minutes=30),
            )
            is not None
        )
        record_qualification(
            session,
            provider="dataforseo-live",
            environment="production",
            adapter_version=DATAFORSEO_ADAPTER_VERSION,
            checks={"account_access": False},
            ttl_hours=1,
            now=now + timedelta(minutes=31),
        )
        assert (
            current_qualification(
                session,
                provider="dataforseo-live",
                environment="production",
                adapter_version=DATAFORSEO_ADAPTER_VERSION,
                now=now + timedelta(minutes=32),
            )
            is None
        )
        assert (
            current_qualification(
                session,
                provider="dataforseo-live",
                environment="production",
                adapter_version=DATAFORSEO_ADAPTER_VERSION,
                now=now + timedelta(hours=2),
            )
            is None
        )
