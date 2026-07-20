from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from rank_rent.db.orm import ScanRunORM, WorkerHeartbeatORM
from rank_rent.observability.metrics import (
    ACTIVE_JOBS,
    CANCELLED_JOBS,
    DATABASE_AVAILABLE,
    FAILED_JOBS,
    OLDEST_QUEUED_SECONDS,
    QUEUE_DEPTH,
    STALE_JOBS,
    WORKER_HEARTBEAT_AGE,
)
from rank_rent.runtime import ConfigurationError, validate_environment
from rank_rent.settings import Settings


def database_health(session: Session) -> dict[str, Any]:
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        DATABASE_AVAILABLE.set(0)
        return {"status": "unavailable", "error_type": type(exc).__name__}
    DATABASE_AVAILABLE.set(1)
    return {"status": "ok"}


def worker_health(session: Session, settings: Settings) -> dict[str, Any]:
    now = datetime.now(UTC)
    queued = session.scalar(
        select(func.count()).select_from(ScanRunORM).where(ScanRunORM.status == "queued")
    ) or 0
    running = session.scalar(
        select(func.count()).select_from(ScanRunORM).where(ScanRunORM.status == "running")
    ) or 0
    failed = session.scalar(
        select(func.count()).select_from(ScanRunORM).where(ScanRunORM.status == "failed")
    ) or 0
    cancelled = session.scalar(
        select(func.count()).select_from(ScanRunORM).where(ScanRunORM.status == "cancelled")
    ) or 0
    oldest = session.scalar(
        select(ScanRunORM.created_at)
        .where(ScanRunORM.status == "queued")
        .order_by(ScanRunORM.created_at)
        .limit(1)
    )
    heartbeat = session.scalar(
        select(WorkerHeartbeatORM.last_seen_at)
        .where(WorkerHeartbeatORM.status == "running")
        .order_by(WorkerHeartbeatORM.last_seen_at.desc())
        .limit(1)
    )
    queue_age = max(0.0, (now - _aware(oldest)).total_seconds()) if oldest else 0.0
    heartbeat_age = (
        max(0.0, (now - _aware(heartbeat)).total_seconds())
        if heartbeat
        else settings.scan_worker_stale_after_seconds + 1.0
    )
    stale = session.scalar(
        select(func.count())
        .select_from(ScanRunORM)
        .where(
            ScanRunORM.status == "running",
            ScanRunORM.heartbeat_at < now - settings.worker_stale_after,
        )
    ) or 0
    QUEUE_DEPTH.set(queued)
    ACTIVE_JOBS.set(running)
    FAILED_JOBS.set(failed)
    CANCELLED_JOBS.set(cancelled)
    OLDEST_QUEUED_SECONDS.set(queue_age)
    WORKER_HEARTBEAT_AGE.set(heartbeat_age)
    STALE_JOBS.set(stale)
    worker_available = heartbeat is not None and heartbeat_age <= settings.scan_worker_stale_after_seconds
    return {
        "status": "ok" if stale == 0 and worker_available else "degraded",
        "worker_available": worker_available,
        "queue_depth": queued,
        "active_jobs": running,
        "oldest_queued_seconds": round(queue_age, 3),
        "heartbeat_age_seconds": round(heartbeat_age, 3),
        "stale_jobs": stale,
    }


def dependency_health(session: Session, settings: Settings) -> dict[str, Any]:
    database = database_health(session)
    try:
        validate_environment(settings)
        configuration: dict[str, Any] = {"status": "ok"}
    except ConfigurationError as exc:
        configuration = {"status": "invalid", "message": str(exc)}
    worker = worker_health(session, settings)
    required_ok = database["status"] == "ok" and configuration["status"] == "ok"
    if settings.worker_required:
        required_ok = required_ok and worker["status"] == "ok"
    return {
        "status": "ok" if required_ok else "unavailable",
        "database": database,
        "worker": worker,
        "configuration": configuration,
        "paid_provider_probe_performed": False,
    }


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
