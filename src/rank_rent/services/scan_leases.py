from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import ScanRunORM


class ScanLeaseLost(RuntimeError):
    """Raised when an asynchronous scan worker no longer owns its durable lease."""


@dataclass(frozen=True)
class ScanExecutionLease:
    scan_id: int
    worker_id: str
    lease_token: str


def assert_current_scan_lease(
    session: Session,
    lease: ScanExecutionLease,
    *,
    now: datetime | None = None,
    lock: bool = False,
) -> None:
    checked_at = now or datetime.now(UTC)
    statement = select(
            ScanRunORM.status,
            ScanRunORM.worker_id,
            ScanRunORM.lease_token,
            ScanRunORM.lease_expires_at,
        ).where(ScanRunORM.id == lease.scan_id)
    if lock:
        statement = statement.with_for_update()
    row = session.execute(statement).one_or_none()
    if row is None:
        raise ScanLeaseLost(f"Scan {lease.scan_id} no longer exists.")
    expires_at = _aware_utc(row.lease_expires_at) if row.lease_expires_at else None
    if (
        row.status != "running"
        or row.worker_id != lease.worker_id
        or row.lease_token != lease.lease_token
        or expires_at is None
        or expires_at <= checked_at
    ):
        raise ScanLeaseLost(
            f"Worker {lease.worker_id} no longer owns the active lease for scan {lease.scan_id}."
        )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
