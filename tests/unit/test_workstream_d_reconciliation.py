from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import (
    ApiCallORM,
    BillingReconciliationORM,
    ImmutableQualificationEvidenceError,
)
from rank_rent.services.billing import reconcile_billing_csv
from rank_rent.services.qualification import (
    DATAFORSEO_ADAPTER_VERSION,
    REQUIRED_QUALIFICATION_CHECKS,
    current_qualification,
    execute_qualification,
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


def test_manual_qualification_is_audited_but_never_unlocks_production() -> None:
    Session = make_session()
    now = datetime.now(UTC)
    with Session() as session:
        imported = record_qualification(
            session,
            provider="dataforseo-live",
            environment="production",
            adapter_version=DATAFORSEO_ADAPTER_VERSION,
            checks={name: True for name in REQUIRED_QUALIFICATION_CHECKS},
            ttl_hours=24,
            executed_by="operator@example.com",
            override_reason="Historical qualification import",
            now=now,
        )
        assert imported.status == "passed"
        assert imported.execution_method == "manual_import"
        assert imported.gate_eligible is False
        assert imported.executed_by == "operator@example.com"
        assert imported.override_reason == "Historical qualification import"
        imported.notes = "attempted rewrite"
        with pytest.raises(ImmutableQualificationEvidenceError):
            session.commit()
        session.rollback()
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


def test_executable_qualification_requires_evidence_and_expires() -> None:
    Session = make_session()
    now = datetime.now(UTC)

    class Executor:
        async def execute_check(self, check_name: str) -> dict[str, object]:
            return {
                "passed": check_name != "schema_drift",
                "source": "executed-test-probe",
            }

    class PassingExecutor:
        async def execute_check(self, check_name: str) -> dict[str, object]:
            return {
                "passed": True,
                "source": "executed-test-probe",
                "check": check_name,
            }

    with Session() as session:
        failed = asyncio.run(
            execute_qualification(
                session,
                provider="dataforseo-live",
                environment="production",
                adapter_version=DATAFORSEO_ADAPTER_VERSION,
                executor=Executor(),
                ttl_hours=1,
                executed_by="test-suite",
                now=now,
            )
        )
        assert failed.status == "failed"
        assert failed.gate_eligible is False
        passed = asyncio.run(
            execute_qualification(
                session,
                provider="dataforseo-live",
                environment="production",
                adapter_version=DATAFORSEO_ADAPTER_VERSION,
                executor=PassingExecutor(),
                ttl_hours=1,
                executed_by="test-suite",
                now=now + timedelta(minutes=1),
            )
        )
        assert passed.status == "passed"
        assert passed.execution_method == "executable_runner"
        assert passed.gate_eligible is True
        assert passed.evidence_sha256 is not None
        assert len(passed.evidence_sha256) == 64
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
        failed_again = asyncio.run(
            execute_qualification(
                session,
                provider="dataforseo-live",
                environment="production",
                adapter_version=DATAFORSEO_ADAPTER_VERSION,
                executor=Executor(),
                ttl_hours=1,
                executed_by="test-suite",
                now=now + timedelta(minutes=31),
            )
        )
        assert failed_again.status == "failed"
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
