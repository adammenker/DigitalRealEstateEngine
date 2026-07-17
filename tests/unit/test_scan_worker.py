from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import ScanRunORM
from rank_rent.services.scan_worker import (
    active_retry_for_scan,
    claim_next_scan,
    recover_stale_scans,
)


def session_factory() -> sessionmaker:
    engine = make_engine("sqlite:///:memory:")
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

        assert claimed == first.id
        assert claimed_again == second.id
        assert session.get(ScanRunORM, first.id).worker_id == "worker-a"
        assert session.get(ScanRunORM, second.id).worker_id == "worker-b"


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

        assert claimed == queued.id
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

        recovered = recover_stale_scans(session, stale_after_seconds=30, now=now)

        assert recovered == 1
        recovered_scan = session.get(ScanRunORM, async_scan.id)
        assert recovered_scan.status == "queued"
        assert recovered_scan.progress_stage == "queued"
        assert recovered_scan.worker_id is None
        assert recovered_scan.heartbeat_at is None
        assert recovered_scan.partial_outputs["recovered_from_worker_timeout"] is True
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
