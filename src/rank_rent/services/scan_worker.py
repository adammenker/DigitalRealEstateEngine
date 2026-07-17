from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from rank_rent.db.base import SessionLocal
from rank_rent.db.orm import ScanRunORM
from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.services.scanner import ScanCancelled, ScanPipeline

logger = logging.getLogger(__name__)

ACTIVE_SCAN_STATUSES = {"queued", "running"}
TERMINAL_SCAN_STATUSES = {"completed", "failed", "cancelled"}
WORKER_SCAN_SOURCE = "manual_async"

SessionFactory = Callable[[], Session]


def build_worker_id() -> str:
    host = socket.gethostname() or "local"
    return f"{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def active_retry_for_scan(session: Session, source_scan_run_id: int) -> ScanRunORM | None:
    return session.scalars(
        select(ScanRunORM)
        .where(
            ScanRunORM.source_scan_run_id == source_scan_run_id,
            ScanRunORM.status.in_(ACTIVE_SCAN_STATUSES),
        )
        .order_by(ScanRunORM.id.desc())
        .limit(1)
    ).first()


def recover_stale_scans(
    session: Session,
    *,
    stale_after_seconds: float,
    now: datetime | None = None,
) -> int:
    recovered_at = now or datetime.now(UTC)
    cutoff = recovered_at - timedelta(seconds=stale_after_seconds)
    stale_scans = session.scalars(
        select(ScanRunORM)
        .where(
            ScanRunORM.source == WORKER_SCAN_SOURCE,
            ScanRunORM.status == "running",
            ScanRunORM.completed_at.is_(None),
            or_(ScanRunORM.heartbeat_at.is_(None), ScanRunORM.heartbeat_at < cutoff),
        )
        .order_by(ScanRunORM.id)
    ).all()
    for scan in stale_scans:
        previous_stage = scan.progress_stage
        if scan.cancel_requested:
            scan.status = "cancelled"
            scan.progress_stage = "cancelled"
            scan.completed_at = recovered_at
            scan.partial_outputs = {
                **(scan.partial_outputs or {}),
                "cancelled": True,
                "cancelled_after_worker_timeout": True,
                "cancelled_at": recovered_at.isoformat(),
            }
        else:
            scan.status = "queued"
            scan.progress_stage = "queued"
            scan.started_at = None
            scan.completed_at = None
            scan.error_summary = None
            scan.partial_outputs = {
                **(scan.partial_outputs or {}),
                "recovered_from_worker_timeout": True,
                "recovered_from_stage": previous_stage,
                "recovered_at": recovered_at.isoformat(),
            }
        scan.worker_id = None
        scan.claimed_at = None
        scan.heartbeat_at = None
    if stale_scans:
        session.commit()
    return len(stale_scans)


def claim_next_scan(session: Session, *, worker_id: str) -> int | None:
    queued_ids = session.scalars(
        select(ScanRunORM.id)
        .where(ScanRunORM.source == WORKER_SCAN_SOURCE, ScanRunORM.status == "queued")
        .order_by(ScanRunORM.id)
        .limit(10)
    ).all()
    for scan_id in queued_ids:
        claimed_at = datetime.now(UTC)
        cancelled = session.execute(
            update(ScanRunORM)
            .where(
                ScanRunORM.id == scan_id,
                ScanRunORM.status == "queued",
                ScanRunORM.cancel_requested.is_(True),
            )
            .values(
                status="cancelled",
                progress_stage="cancelled",
                completed_at=claimed_at,
                worker_id=None,
                heartbeat_at=None,
            )
        )
        if _rowcount(cancelled):
            session.commit()
            session.expire_all()
            continue

        claimed = session.execute(
            update(ScanRunORM)
            .where(
                ScanRunORM.id == scan_id,
                ScanRunORM.status == "queued",
                ScanRunORM.cancel_requested.is_(False),
            )
            .values(
                status="running",
                progress_stage="planning",
                started_at=claimed_at,
                completed_at=None,
                error_summary=None,
                worker_id=worker_id,
                claimed_at=claimed_at,
                heartbeat_at=claimed_at,
            )
        )
        if not _rowcount(claimed):
            session.rollback()
            continue
        session.commit()
        session.expire_all()
        _record_claim_metadata(session, scan_id, worker_id, claimed_at)
        return scan_id
    return None


async def run_scan_by_id(
    scan_id: int,
    *,
    worker_id: str,
    session_factory: SessionFactory | None = None,
    heartbeat_seconds: float = 5.0,
) -> None:
    factory = session_factory or SessionLocal
    with factory() as session:
        scan = session.get(ScanRunORM, scan_id)
        if scan is None:
            logger.warning("Worker %s could not find claimed scan %s.", worker_id, scan_id)
            return
        if scan.status != "running" or scan.worker_id != worker_id:
            logger.info(
                "Worker %s skipped scan %s because it is owned by %s with status %s.",
                worker_id,
                scan_id,
                scan.worker_id,
                scan.status,
            )
            return
        request = scan.request_parameters or {}
        service_payload = request.get("service_payload")
        market_payload = request.get("market_payload")
        data_mode = str(request.get("data_mode") or scan.data_mode)
        if not isinstance(service_payload, dict) or not isinstance(market_payload, dict):
            _fail_claimed_scan(
                session,
                scan,
                worker_id,
                "Queued scan is missing structured service or market metadata.",
            )
            return
        service = ServiceFamily(**service_payload)
        market = Market(**market_payload)

    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat_scan(
            scan_id,
            worker_id=worker_id,
            stop_event=heartbeat_stop,
            session_factory=factory,
            heartbeat_seconds=heartbeat_seconds,
        )
    )
    try:
        with factory() as session:
            await ScanPipeline(session, data_mode=data_mode).run(
                service,
                market,
                source=WORKER_SCAN_SOURCE,
                existing_scan_id=scan_id,
            )
    except ScanCancelled:
        logger.info("Durable worker scan %s was cancelled.", scan_id)
    except Exception:
        logger.exception("Durable worker scan %s failed unexpectedly.", scan_id)
    finally:
        heartbeat_stop.set()
        with suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(heartbeat_task, timeout=max(1.0, heartbeat_seconds + 1.0))
        _clear_terminal_worker_claim(factory, scan_id)


async def scan_worker_loop(
    stop_event: asyncio.Event,
    *,
    session_factory: SessionFactory | None = None,
    worker_id: str | None = None,
    poll_seconds: float = 1.0,
    heartbeat_seconds: float = 5.0,
    stale_after_seconds: float = 30.0,
) -> None:
    factory = session_factory or SessionLocal
    active_worker_id = worker_id or build_worker_id()
    logger.info("Scan worker %s started.", active_worker_id)
    try:
        while not stop_event.is_set():
            with factory() as session:
                recovered = recover_stale_scans(
                    session,
                    stale_after_seconds=stale_after_seconds,
                )
                scan_id = claim_next_scan(session, worker_id=active_worker_id)
            if recovered:
                logger.info(
                    "Scan worker %s recovered %s stale scan(s).",
                    active_worker_id,
                    recovered,
                )
            if scan_id is not None:
                await run_scan_by_id(
                    scan_id,
                    worker_id=active_worker_id,
                    session_factory=factory,
                    heartbeat_seconds=heartbeat_seconds,
                )
                continue
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
    finally:
        logger.info("Scan worker %s stopped.", active_worker_id)


def _record_claim_metadata(
    session: Session,
    scan_id: int,
    worker_id: str,
    claimed_at: datetime,
) -> None:
    scan = session.get(ScanRunORM, scan_id)
    if scan is None:
        return
    scan.partial_outputs = {
        **(scan.partial_outputs or {}),
        "claimed_by": worker_id,
        "claimed_at": claimed_at.isoformat(),
    }
    session.commit()


def _fail_claimed_scan(
    session: Session,
    scan: ScanRunORM,
    worker_id: str,
    error_summary: str,
) -> None:
    if scan.worker_id != worker_id:
        return
    now = datetime.now(UTC)
    scan.status = "failed"
    scan.progress_stage = "failed"
    scan.error_summary = error_summary
    scan.completed_at = now
    scan.worker_id = None
    scan.heartbeat_at = None
    scan.partial_outputs = {
        **(scan.partial_outputs or {}),
        "worker_error": error_summary,
        "failed_at": now.isoformat(),
    }
    session.commit()


async def _heartbeat_scan(
    scan_id: int,
    *,
    worker_id: str,
    stop_event: asyncio.Event,
    session_factory: SessionFactory,
    heartbeat_seconds: float,
) -> None:
    while not stop_event.is_set():
        with session_factory() as session:
            result = session.execute(
                update(ScanRunORM)
                .where(
                    ScanRunORM.id == scan_id,
                    ScanRunORM.status == "running",
                    ScanRunORM.worker_id == worker_id,
                )
                .values(heartbeat_at=datetime.now(UTC))
            )
            session.commit()
            if not _rowcount(result):
                return
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=heartbeat_seconds)


def _clear_terminal_worker_claim(session_factory: SessionFactory, scan_id: int) -> None:
    with session_factory() as session:
        scan = session.get(ScanRunORM, scan_id)
        if scan is None or scan.status not in TERMINAL_SCAN_STATUSES:
            return
        scan.worker_id = None
        scan.heartbeat_at = None
        session.commit()


def _rowcount(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)
