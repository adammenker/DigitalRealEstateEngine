from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import ScanRunORM
from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.services.scan_worker import (
    active_retry_for_scan,
    claim_next_scan,
    recover_stale_scans,
    retry_delay_seconds,
    run_scan_by_id,
)
from rank_rent.settings import Settings


def session_factory() -> sessionmaker:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def file_session_factory(path: Path) -> sessionmaker:
    engine = make_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_worker_claims_queued_scan_once() -> None:
    Session = session_factory()
    with Session() as session:
        first = ScanRunORM(source="manual_async", status="queued")
        second = ScanRunORM(source="manual_async", status="queued")
        session.add_all([first, second])
        session.commit()

        claimed = claim_next_scan(session, worker_id="worker-a")
        claimed_again = claim_next_scan(session, worker_id="worker-b")

        assert claimed is not None and claimed.scan_id == first.id
        assert claimed_again is not None and claimed_again.scan_id == second.id
        assert claimed.lease_token != claimed_again.lease_token
        assert session.get(ScanRunORM, first.id).worker_id == "worker-a"
        assert session.get(ScanRunORM, second.id).worker_id == "worker-b"


def test_concurrent_workers_cannot_claim_the_same_lease(tmp_path: Path) -> None:
    Session = file_session_factory(tmp_path / "worker-claims.db")
    with Session() as session:
        scan = ScanRunORM(source="manual_async", status="queued")
        session.add(scan)
        session.commit()
        scan_id = scan.id

    def claim(worker_id: str):
        with Session() as session:
            return claim_next_scan(session, worker_id=worker_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(claim, ("worker-a", "worker-b")))

    claimed = [lease for lease in leases if lease is not None]
    assert len(claimed) == 1
    assert claimed[0].scan_id == scan_id
    with Session() as session:
        stored = session.get(ScanRunORM, scan_id)
        assert stored is not None
        assert stored.status == "running"
        assert stored.lease_token == claimed[0].lease_token


def test_worker_cancels_queued_scan_before_claiming_next() -> None:
    Session = session_factory()
    with Session() as session:
        cancelled = ScanRunORM(
            source="manual_async",
            status="queued",
            cancel_requested=True,
        )
        queued = ScanRunORM(source="manual_async", status="queued")
        session.add_all([cancelled, queued])
        session.commit()

        claimed = claim_next_scan(session, worker_id="worker-a")

        assert claimed is not None and claimed.scan_id == queued.id
        assert session.get(ScanRunORM, cancelled.id).status == "cancelled"
        assert session.get(ScanRunORM, queued.id).status == "running"


def test_recover_stale_worker_scan_requeues_without_touching_sync_scan() -> None:
    Session = session_factory()
    now = datetime.now(UTC)
    stale_heartbeat = now - timedelta(minutes=5)
    with Session() as session:
        async_scan = ScanRunORM(
            source="manual_async",
            status="running",
            progress_stage="fetching_metrics",
            worker_id="old-worker",
            claimed_at=stale_heartbeat,
            heartbeat_at=stale_heartbeat,
        )
        sync_scan = ScanRunORM(
            source="manual",
            status="running",
            progress_stage="fetching_metrics",
        )
        session.add_all([async_scan, sync_scan])
        session.commit()

        recovered = recover_stale_scans(
            session,
            stale_after_seconds=30,
            settings=Settings(scan_worker_retry_base_seconds=2),
            now=now,
        )

        assert recovered == 1
        recovered_scan = session.get(ScanRunORM, async_scan.id)
        assert recovered_scan.status == "queued"
        assert recovered_scan.progress_stage == "queued"
        assert recovered_scan.worker_id is None
        assert recovered_scan.heartbeat_at is None
        assert recovered_scan.retry_count == 1
        assert recovered_scan.next_attempt_at is not None
        assert recovered_scan.partial_outputs["retry_classification"] == "retryable"
        assert session.get(ScanRunORM, sync_scan.id).status == "running"


def test_recover_stale_cancelled_scan_finishes_without_requeue() -> None:
    Session = session_factory()
    now = datetime.now(UTC)
    stale_heartbeat = now - timedelta(minutes=5)
    with Session() as session:
        scan = ScanRunORM(
            source="manual_async",
            status="running",
            progress_stage="discovering_keywords",
            worker_id="old-worker",
            heartbeat_at=stale_heartbeat,
            cancel_requested=True,
        )
        session.add(scan)
        session.commit()

        recovered = recover_stale_scans(session, stale_after_seconds=30, now=now)

        assert recovered == 1
        recovered_scan = session.get(ScanRunORM, scan.id)
        assert recovered_scan.status == "cancelled"
        assert recovered_scan.progress_stage == "cancelled"
        assert recovered_scan.worker_id is None
        assert recovered_scan.partial_outputs["cancelled_after_worker_timeout"] is True


def test_active_retry_for_scan_returns_existing_queued_retry() -> None:
    Session = session_factory()
    with Session() as session:
        source = ScanRunORM(source="manual_async", status="failed")
        retry = ScanRunORM(
            source="manual_async",
            status="queued",
            source_scan_run_id=1,
        )
        session.add(source)
        session.flush()
        retry.source_scan_run_id = source.id
        session.add(retry)
        session.commit()

        assert active_retry_for_scan(session, source.id).id == retry.id


def test_exponential_backoff_uses_bounded_jitter() -> None:
    assert retry_delay_seconds(1, base_seconds=2, maximum_seconds=30, random_value=0) == 1
    assert retry_delay_seconds(3, base_seconds=2, maximum_seconds=30, random_value=1) == 8
    assert retry_delay_seconds(10, base_seconds=2, maximum_seconds=30, random_value=1) == 30


def test_stale_poison_job_is_quarantined_at_max_attempts() -> None:
    Session = session_factory()
    now = datetime.now(UTC)
    with Session() as session:
        scan = ScanRunORM(
            source="manual_async",
            status="running",
            worker_id="dead-worker",
            heartbeat_at=now - timedelta(minutes=5),
            retry_count=2,
            max_attempts=3,
        )
        session.add(scan)
        session.commit()

        recover_stale_scans(
            session,
            stale_after_seconds=30,
            settings=Settings(),
            now=now,
        )

        assert scan.status == "quarantined"
        assert scan.quarantined_at == now
        assert scan.partial_outputs["poison_job"] is True


def _queued_request() -> dict[str, object]:
    return {
        "service_payload": ServiceFamily(
            id="drywall",
            display_name="Drywall",
            seed_queries=["drywall"],
        ).model_dump(mode="json"),
        "market_payload": Market(
            id="st-louis-mo",
            display_name="St. Louis, MO",
        ).model_dump(mode="json"),
        "data_mode": "fixture",
        "scan_profile": "testing",
    }


def test_terminal_scan_clears_its_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    Session = session_factory()
    with Session() as session:
        scan = ScanRunORM(
            source="manual_async",
            status="queued",
            request_parameters=_queued_request(),
        )
        session.add(scan)
        session.commit()
        scan_id = scan.id
        lease = claim_next_scan(session, worker_id="worker-a")
    assert lease is not None

    class CompletingPipeline:
        def __init__(self, session, **_: object) -> None:
            self.session = session

        async def run(self, *_: object, existing_scan_id: int, **__: object) -> dict[str, object]:
            stored = self.session.get(ScanRunORM, existing_scan_id)
            assert stored is not None
            stored.status = "completed"
            stored.progress_stage = "completed"
            stored.completed_at = datetime.now(UTC)
            self.session.commit()
            return {}

    monkeypatch.setattr("rank_rent.services.scan_worker.ScanPipeline", CompletingPipeline)
    asyncio.run(
        run_scan_by_id(
            lease,
            session_factory=Session,
            heartbeat_seconds=0.01,
            lease_seconds=1,
            settings=Settings(),
        )
    )

    with Session() as session:
        stored = session.get(ScanRunORM, scan_id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.worker_id is None
        assert stored.lease_token is None
        assert stored.lease_expires_at is None


def test_retryable_failure_requeues_and_releases_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Session = session_factory()
    with Session() as session:
        scan = ScanRunORM(
            source="manual_async",
            status="queued",
            request_parameters=_queued_request(),
        )
        session.add(scan)
        session.commit()
        scan_id = scan.id
        lease = claim_next_scan(session, worker_id="worker-a")
    assert lease is not None

    class FailingPipeline:
        def __init__(self, session, **_: object) -> None:
            self.session = session

        async def run(self, *_: object, existing_scan_id: int, **__: object) -> dict[str, object]:
            stored = self.session.get(ScanRunORM, existing_scan_id)
            assert stored is not None
            stored.status = "failed"
            stored.progress_stage = "failed"
            stored.partial_outputs = {"failed_stage": "fetching_serps"}
            self.session.commit()
            raise TimeoutError("transient provider timeout")

    monkeypatch.setattr("rank_rent.services.scan_worker.ScanPipeline", FailingPipeline)
    asyncio.run(
        run_scan_by_id(
            lease,
            session_factory=Session,
            heartbeat_seconds=0.01,
            lease_seconds=1,
            settings=Settings(scan_worker_retry_base_seconds=1),
        )
    )

    with Session() as session:
        stored = session.get(ScanRunORM, scan_id)
        assert stored is not None
        assert stored.status == "queued"
        assert stored.retry_count == 1
        assert stored.next_attempt_at is not None
        assert stored.worker_id is None
        assert stored.lease_token is None
