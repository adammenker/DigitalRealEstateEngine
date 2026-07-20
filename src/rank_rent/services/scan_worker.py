from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from rank_rent.db.base import WorkerSessionLocal
from rank_rent.db.orm import ScanRunORM
from rank_rent.domain.models import Market, ServiceFamily
from rank_rent.integrations.dataforseo.live import (
    DataForSEOAuthenticationError,
    DataForSEOError,
    DataForSEOPlanError,
    DataForSEORateLimitError,
    DataForSEOSchemaError,
)
from rank_rent.runtime import ConfigurationError
from rank_rent.services.cost_controls import CircuitOpenError
from rank_rent.services.scanner import ScanCancelled, ScanPipeline
from rank_rent.settings import Settings, get_settings

logger = logging.getLogger(__name__)

ACTIVE_SCAN_STATUSES = {"queued", "running"}
TERMINAL_SCAN_STATUSES = {"completed", "failed", "cancelled", "quarantined"}
WORKER_SCAN_SOURCE = "manual_async"
WORKER_SCAN_SOURCES = {WORKER_SCAN_SOURCE, "promotion_async"}

SessionFactory = Callable[[], Session]


@dataclass(frozen=True)
class WorkerLease:
    scan_id: int
    worker_id: str
    lease_token: str


def build_worker_id(slot: int | None = None) -> str:
    host = socket.gethostname() or "local"
    suffix = f":{slot}" if slot is not None else ""
    return f"{host}:{os.getpid()}{suffix}:{uuid.uuid4().hex[:8]}"


def active_retry_for_scan(session: Session, source_scan_run_id: int) -> ScanRunORM | None:
    return session.scalars(
        select(ScanRunORM)
        .where(
            or_(
                ScanRunORM.id == source_scan_run_id,
                ScanRunORM.source_scan_run_id == source_scan_run_id,
            ),
            ScanRunORM.status.in_(ACTIVE_SCAN_STATUSES),
        )
        .order_by(ScanRunORM.id.desc())
        .limit(1)
    ).first()


def retry_delay_seconds(
    retry_count: int,
    *,
    base_seconds: float,
    maximum_seconds: float,
    random_value: float | None = None,
) -> float:
    capped = min(maximum_seconds, base_seconds * float(2 ** max(0, retry_count - 1)))
    jitter = random.random() if random_value is None else min(max(random_value, 0.0), 1.0)
    return capped * (0.5 + jitter * 0.5)


def recover_stale_scans(
    session: Session,
    *,
    stale_after_seconds: float,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    active_settings = settings or get_settings()
    recovered_at = now or datetime.now(UTC)
    cutoff = recovered_at - timedelta(seconds=stale_after_seconds)
    stale_scans = session.scalars(
        select(ScanRunORM)
        .where(
            ScanRunORM.source.in_(WORKER_SCAN_SOURCES),
            ScanRunORM.status == "running",
            ScanRunORM.completed_at.is_(None),
            or_(
                ScanRunORM.lease_expires_at < recovered_at,
                ScanRunORM.lease_expires_at.is_(None),
                ScanRunORM.heartbeat_at < cutoff,
                ScanRunORM.heartbeat_at.is_(None),
            ),
        )
        .order_by(ScanRunORM.id)
        .with_for_update(skip_locked=True)
    ).all()
    for scan in stale_scans:
        previous_stage = scan.progress_stage
        if scan.cancel_requested:
            _cancel_scan(scan, recovered_at, timed_out=True)
        else:
            _retry_or_quarantine(
                scan,
                error_summary="Worker lease expired before completion.",
                settings=active_settings,
                now=recovered_at,
                failed_stage=previous_stage,
            )
        _clear_lease(scan)
    if stale_scans:
        session.commit()
    return len(stale_scans)


def claim_next_scan(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: float = 30.0,
    now: datetime | None = None,
) -> WorkerLease | None:
    claimed_at = now or datetime.now(UTC)
    queued_ids = session.scalars(
        select(ScanRunORM.id)
        .where(
            ScanRunORM.source.in_(WORKER_SCAN_SOURCES),
            ScanRunORM.status == "queued",
            or_(ScanRunORM.next_attempt_at.is_(None), ScanRunORM.next_attempt_at <= claimed_at),
        )
        .order_by(ScanRunORM.id)
        .limit(10)
    ).all()
    for scan_id in queued_ids:
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
                lease_token=None,
                lease_expires_at=None,
            )
        )
        if _rowcount(cancelled):
            session.commit()
            session.expire_all()
            continue

        lease_token = uuid.uuid4().hex
        claimed = session.execute(
            update(ScanRunORM)
            .where(
                ScanRunORM.id == scan_id,
                ScanRunORM.status == "queued",
                ScanRunORM.cancel_requested.is_(False),
                or_(ScanRunORM.next_attempt_at.is_(None), ScanRunORM.next_attempt_at <= claimed_at),
            )
            .values(
                status="running",
                progress_stage="planning",
                started_at=claimed_at,
                completed_at=None,
                error_summary=None,
                worker_id=worker_id,
                lease_token=lease_token,
                claimed_at=claimed_at,
                heartbeat_at=claimed_at,
                lease_expires_at=claimed_at + timedelta(seconds=lease_seconds),
                next_attempt_at=None,
            )
        )
        if not _rowcount(claimed):
            session.rollback()
            continue
        session.commit()
        scan = session.get(ScanRunORM, scan_id)
        if scan is not None:
            scan.partial_outputs = {
                **(scan.partial_outputs or {}),
                "claimed_by": worker_id,
                "claimed_at": claimed_at.isoformat(),
                "lease_token": lease_token,
            }
            session.commit()
            session.expire_all()
        return WorkerLease(scan_id=scan_id, worker_id=worker_id, lease_token=lease_token)
    return None


async def run_scan_by_id(
    lease: WorkerLease,
    *,
    session_factory: SessionFactory | None = None,
    heartbeat_seconds: float = 5.0,
    lease_seconds: float = 30.0,
    settings: Settings | None = None,
) -> None:
    factory = session_factory or WorkerSessionLocal
    active_settings = settings or get_settings()
    with factory() as session:
        scan = session.get(ScanRunORM, lease.scan_id)
        if scan is None:
            logger.warning(
                "Worker %s could not find claimed scan %s.", lease.worker_id, lease.scan_id
            )
            return
        if not _owns(scan, lease):
            logger.info(
                "Worker %s skipped scan %s because its lease is no longer current.",
                lease.worker_id,
                lease.scan_id,
            )
            return
        request = scan.request_parameters or {}
        service_payload = request.get("service_payload")
        market_payload = request.get("market_payload")
        data_mode = str(request.get("data_mode") or scan.data_mode)
        scan_profile = str(request.get("scan_profile") or scan.scan_profile)
        scan_source = scan.source
        if not isinstance(service_payload, dict) or not isinstance(market_payload, dict):
            _fail_without_retry(
                session,
                scan,
                lease,
                "Queued scan is missing structured service or market metadata.",
            )
            return
        service = ServiceFamily(**service_payload)
        market = Market(**market_payload)

    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat_scan(
            lease,
            stop_event=heartbeat_stop,
            session_factory=factory,
            heartbeat_seconds=heartbeat_seconds,
            lease_seconds=lease_seconds,
        )
    )
    error: Exception | None = None
    try:
        with factory() as session:
            await ScanPipeline(
                session,
                data_mode=data_mode,
                scan_profile=scan_profile,
            ).run(
                service,
                market,
                source=scan_source,
                existing_scan_id=lease.scan_id,
            )
    except ScanCancelled:
        logger.info("Durable worker scan %s was cancelled.", lease.scan_id)
    except Exception as exc:
        error = exc
        logger.exception("Durable worker scan %s failed unexpectedly.", lease.scan_id)
    finally:
        heartbeat_stop.set()
        with suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(heartbeat_task, timeout=max(1.0, heartbeat_seconds + 1.0))
        with factory() as session:
            scan = session.get(ScanRunORM, lease.scan_id)
            if scan is not None and _lease_matches(scan, lease):
                if error is not None and scan.status == "failed":
                    if is_retryable_error(error):
                        _retry_or_quarantine(
                            scan,
                            error_summary=str(error),
                            settings=active_settings,
                            now=datetime.now(UTC),
                            failed_stage=str(
                                (scan.partial_outputs or {}).get("failed_stage") or "unknown"
                            ),
                        )
                    else:
                        scan.partial_outputs = {
                            **(scan.partial_outputs or {}),
                            "retry_classification": "non_retryable",
                        }
                if scan.status in TERMINAL_SCAN_STATUSES or scan.status == "queued":
                    _clear_lease(scan)
                session.commit()


async def scan_worker_loop(
    stop_event: asyncio.Event,
    *,
    session_factory: SessionFactory | None = None,
    worker_id: str | None = None,
    poll_seconds: float = 1.0,
    heartbeat_seconds: float = 5.0,
    stale_after_seconds: float = 30.0,
    settings: Settings | None = None,
) -> None:
    factory = session_factory or WorkerSessionLocal
    active_settings = settings or get_settings()
    active_worker_id = worker_id or build_worker_id()
    logger.info("Scan worker %s started.", active_worker_id)
    try:
        while not stop_event.is_set():
            with factory() as session:
                recovered = recover_stale_scans(
                    session,
                    stale_after_seconds=stale_after_seconds,
                    settings=active_settings,
                )
                lease = claim_next_scan(
                    session,
                    worker_id=active_worker_id,
                    lease_seconds=stale_after_seconds,
                )
            if recovered:
                logger.info(
                    "Scan worker %s recovered %s stale scan(s).", active_worker_id, recovered
                )
            if lease is not None:
                await run_scan_by_id(
                    lease,
                    session_factory=factory,
                    heartbeat_seconds=heartbeat_seconds,
                    lease_seconds=stale_after_seconds,
                    settings=active_settings,
                )
                continue
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
    finally:
        logger.info("Scan worker %s stopped.", active_worker_id)


async def run_worker_runtime(
    stop_event: asyncio.Event,
    *,
    concurrency: int,
    session_factory: SessionFactory | None = None,
    settings: Settings | None = None,
) -> None:
    active_settings = settings or get_settings()
    tasks = [
        asyncio.create_task(
            scan_worker_loop(
                stop_event,
                session_factory=session_factory,
                worker_id=build_worker_id(slot),
                poll_seconds=active_settings.scan_worker_poll_seconds,
                heartbeat_seconds=active_settings.scan_worker_heartbeat_seconds,
                stale_after_seconds=active_settings.scan_worker_stale_after_seconds,
                settings=active_settings,
            )
        )
        for slot in range(concurrency)
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        stop_event.set()
        await asyncio.gather(*tasks, return_exceptions=True)


def is_retryable_error(error: Exception) -> bool:
    if isinstance(
        error,
        (
            CircuitOpenError,
            ConfigurationError,
            DataForSEOAuthenticationError,
            DataForSEOPlanError,
            DataForSEOSchemaError,
            ValueError,
        ),
    ):
        return False
    if isinstance(
        error,
        (
            TimeoutError,
            httpx.TimeoutException,
            OperationalError,
            DataForSEORateLimitError,
        ),
    ):
        return True
    if isinstance(error, DataForSEOError):
        message = str(error).lower()
        return any(
            token in message
            for token in ("transient", "timeout", "rate limit", "temporar", "http 5")
        )
    return False


async def _heartbeat_scan(
    lease: WorkerLease,
    *,
    stop_event: asyncio.Event,
    session_factory: SessionFactory,
    heartbeat_seconds: float,
    lease_seconds: float,
) -> None:
    while not stop_event.is_set():
        now = datetime.now(UTC)
        with session_factory() as session:
            result = session.execute(
                update(ScanRunORM)
                .where(
                    ScanRunORM.id == lease.scan_id,
                    ScanRunORM.status == "running",
                    ScanRunORM.worker_id == lease.worker_id,
                    ScanRunORM.lease_token == lease.lease_token,
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                )
            )
            session.commit()
            if not _rowcount(result):
                return
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=heartbeat_seconds)


def _retry_or_quarantine(
    scan: ScanRunORM,
    *,
    error_summary: str,
    settings: Settings,
    now: datetime,
    failed_stage: str,
) -> None:
    scan.retry_count = (scan.retry_count or 0) + 1
    max_attempts = scan.max_attempts or settings.scan_worker_max_attempts
    scan.error_summary = error_summary
    if scan.retry_count >= max_attempts:
        scan.status = "quarantined"
        scan.progress_stage = "quarantined"
        scan.completed_at = now
        scan.quarantined_at = now
        scan.quarantine_reason = error_summary
        scan.partial_outputs = {
            **(scan.partial_outputs or {}),
            "poison_job": True,
            "failed_stage": failed_stage,
            "attempts": scan.retry_count,
        }
        return
    delay = retry_delay_seconds(
        scan.retry_count,
        base_seconds=settings.scan_worker_retry_base_seconds,
        maximum_seconds=settings.scan_worker_retry_max_seconds,
    )
    scan.status = "queued"
    scan.progress_stage = "queued"
    scan.started_at = None
    scan.completed_at = None
    scan.next_attempt_at = now + timedelta(seconds=delay)
    scan.partial_outputs = {
        **(scan.partial_outputs or {}),
        "retry_classification": "retryable",
        "failed_stage": failed_stage,
        "retry_count": scan.retry_count,
        "retry_delay_seconds": delay,
        "next_attempt_at": scan.next_attempt_at.isoformat(),
    }


def _cancel_scan(scan: ScanRunORM, now: datetime, *, timed_out: bool) -> None:
    scan.status = "cancelled"
    scan.progress_stage = "cancelled"
    scan.completed_at = now
    scan.partial_outputs = {
        **(scan.partial_outputs or {}),
        "cancelled": True,
        "cancelled_after_worker_timeout": timed_out,
        "cancelled_at": now.isoformat(),
    }


def _fail_without_retry(
    session: Session,
    scan: ScanRunORM,
    lease: WorkerLease,
    error_summary: str,
) -> None:
    if not _owns(scan, lease):
        return
    now = datetime.now(UTC)
    scan.status = "failed"
    scan.progress_stage = "failed"
    scan.error_summary = error_summary
    scan.completed_at = now
    scan.partial_outputs = {
        **(scan.partial_outputs or {}),
        "worker_error": error_summary,
        "retry_classification": "non_retryable",
        "failed_at": now.isoformat(),
    }
    _clear_lease(scan)
    session.commit()


def _owns(scan: ScanRunORM, lease: WorkerLease) -> bool:
    return (
        scan.status == "running"
        and _lease_matches(scan, lease)
    )


def _lease_matches(scan: ScanRunORM, lease: WorkerLease) -> bool:
    return scan.worker_id == lease.worker_id and scan.lease_token == lease.lease_token


def _clear_lease(scan: ScanRunORM) -> None:
    scan.worker_id = None
    scan.lease_token = None
    scan.claimed_at = None
    scan.heartbeat_at = None
    scan.lease_expires_at = None


def _rowcount(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)
