from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from rank_rent.db.orm import (
    ApiCallORM,
    BillingReconciliationORM,
    ProviderDailyUsageORM,
    ScanRunORM,
)
from rank_rent.services.qualification import current_qualification
from rank_rent.services.scan_leases import ScanExecutionLease, assert_current_scan_lease
from rank_rent.settings import Settings


class CircuitOpenError(RuntimeError):
    """Raised before network access when a durable paid-call policy blocks a request."""


@dataclass(frozen=True)
class UsageReservation:
    usage_date: date
    usage_class: str
    provider: str
    endpoint: str
    estimated_cost_usd: float
    api_call_id: int | None = None


def reserve_provider_call(
    session: Session,
    *,
    settings: Settings,
    provider: str,
    environment: str,
    adapter_version: str,
    endpoint: str,
    estimated_cost_usd: float,
    scan_profile: str,
    cache_miss: bool,
    require_current_qualification: bool = True,
    api_call_id: int | None = None,
    execution_lease: ScanExecutionLease | None = None,
    now: datetime | None = None,
) -> UsageReservation:
    called_at = now or datetime.now(UTC)
    usage_class = _usage_class(environment)
    if execution_lease is not None:
        assert_current_scan_lease(session, execution_lease, now=called_at, lock=True)
    _assert_switches(settings, environment=environment, scan_profile=scan_profile)
    if estimated_cost_usd > settings.single_call_abnormal_cost_usd:
        raise CircuitOpenError(
            f"Estimated endpoint cost ${estimated_cost_usd:.4f} exceeds the abnormal-call limit "
            f"${settings.single_call_abnormal_cost_usd:.4f}."
        )
    api_call = None
    if api_call_id is not None:
        api_call = session.get(ApiCallORM, api_call_id, with_for_update=True)
        if api_call is None:
            raise CircuitOpenError(f"API call attempt {api_call_id} no longer exists.")
        if api_call.reservation_state != "none" or api_call.attempt_state != "prepared":
            raise CircuitOpenError(
                f"API call attempt {api_call_id} is not eligible for a new reservation."
            )

    summary = _locked_bucket(
        session,
        usage_date=called_at.date(),
        usage_class=usage_class,
        provider=provider,
        endpoint="",
    )
    endpoint_bucket = _locked_bucket(
        session,
        usage_date=called_at.date(),
        usage_class=usage_class,
        provider=provider,
        endpoint=endpoint,
    )
    _assert_durable_breakers(
        session,
        settings=settings,
        summary=summary,
        environment=environment,
        provider=provider,
        adapter_version=adapter_version,
        usage_class=usage_class,
        estimated_cost_usd=estimated_cost_usd,
        require_current_qualification=require_current_qualification,
        now=called_at,
    )
    summary.request_count += 1
    summary.reserved_spend_usd += estimated_cost_usd
    endpoint_bucket.request_count += 1
    endpoint_bucket.reserved_spend_usd += estimated_cost_usd
    if cache_miss:
        summary.cache_miss_count += 1
        endpoint_bucket.cache_miss_count += 1
    if api_call is not None:
        api_call.reservation_state = "reserved"
        api_call.reservation_usage_date = called_at.date()
        api_call.reservation_usage_class = usage_class
        api_call.reservation_estimated_cost_usd = estimated_cost_usd
        api_call.attempt_state = "reserved"
    session.commit()
    return UsageReservation(
        usage_date=called_at.date(),
        usage_class=usage_class,
        provider=provider,
        endpoint=endpoint,
        estimated_cost_usd=estimated_cost_usd,
        api_call_id=api_call_id,
    )


def mark_provider_call_submitted(
    session: Session,
    reservation: UsageReservation,
    *,
    execution_lease: ScanExecutionLease | None = None,
    now: datetime | None = None,
) -> None:
    submitted_at = now or datetime.now(UTC)
    if execution_lease is not None:
        assert_current_scan_lease(session, execution_lease, now=submitted_at, lock=True)
    if reservation.api_call_id is None:
        return
    api_call = session.get(ApiCallORM, reservation.api_call_id, with_for_update=True)
    if (
        api_call is None
        or api_call.reservation_state != "reserved"
        or api_call.attempt_state != "reserved"
    ):
        raise CircuitOpenError("Provider call attempt is not reserved for submission.")
    api_call.attempt_state = "in_flight"
    api_call.provider_outcome = "submitted"
    api_call.network_started_at = submitted_at
    api_call.status = "in_flight"
    session.commit()


def finish_provider_call(
    session: Session,
    reservation: UsageReservation,
    *,
    actual_cost_usd: float,
    failed: bool = False,
    schema_drift: bool = False,
    abnormal_cost: bool = False,
    provider_outcome_unknown: bool = False,
    now: datetime | None = None,
) -> None:
    finished_at = now or datetime.now(UTC)
    api_call = (
        session.get(ApiCallORM, reservation.api_call_id, with_for_update=True)
        if reservation.api_call_id is not None
        else None
    )
    if api_call is not None and api_call.reservation_state in {
        "finalized",
        "released",
        "reconciled_unknown",
    }:
        return
    for endpoint in ("", reservation.endpoint):
        bucket = _locked_bucket(
            session,
            usage_date=reservation.usage_date,
            usage_class=reservation.usage_class,
            provider=reservation.provider,
            endpoint=endpoint,
        )
        bucket.reserved_spend_usd = max(
            0.0,
            bucket.reserved_spend_usd - reservation.estimated_cost_usd,
        )
        if provider_outcome_unknown:
            bucket.unreconciled_spend_usd += reservation.estimated_cost_usd
        else:
            bucket.spend_usd += max(0.0, actual_cost_usd)
        if failed:
            bucket.provider_failure_count += 1
        if schema_drift:
            bucket.schema_drift_count += 1
        if abnormal_cost:
            bucket.abnormal_cost_count += 1
    if api_call is not None:
        api_call.reservation_state = (
            "reconciled_unknown" if provider_outcome_unknown else "finalized"
        )
        api_call.reconciled_at = finished_at
        if provider_outcome_unknown:
            api_call.attempt_state = "provider_outcome_unknown"
            api_call.provider_outcome = "unknown"
            api_call.status = "provider_outcome_unknown"
    session.commit()


def reconcile_stale_api_call_attempts(
    session: Session,
    *,
    stale_before: datetime,
    scan_run_id: int | None = None,
    now: datetime | None = None,
    commit: bool = True,
) -> int:
    reconciled_at = now or datetime.now(UTC)
    statement = (
        select(ApiCallORM)
        .where(
            ApiCallORM.attempt_state.in_({"prepared", "reserved", "in_flight"}),
            ApiCallORM.started_at.is_not(None),
            ApiCallORM.started_at < stale_before,
        )
        .order_by(ApiCallORM.id)
        .with_for_update(skip_locked=True)
    )
    if scan_run_id is not None:
        statement = statement.where(ApiCallORM.scan_run_id == scan_run_id)
    rows = list(session.scalars(statement).all())
    for row in rows:
        if row.network_started_at is not None or row.attempt_state == "in_flight":
            _reconcile_attempt_reservation(
                session,
                row,
                unknown=True,
                reconciled_at=reconciled_at,
            )
            row.status = "provider_outcome_unknown"
            row.attempt_state = "provider_outcome_unknown"
            row.provider_outcome = "unknown"
            row.error_type = "StaleProviderAttempt"
            row.error_summary = (
                "Worker stopped after provider submission; outcome requires billing or "
                "provider-side reconciliation and will not be retried automatically."
            )
        else:
            _reconcile_attempt_reservation(
                session,
                row,
                unknown=False,
                reconciled_at=reconciled_at,
            )
            row.status = "failed_before_network"
            row.attempt_state = "failed_before_network"
            row.provider_outcome = "not_sent"
            row.error_type = "StaleProviderAttempt"
            row.error_summary = "Worker stopped before provider submission; reservation released."
        row.completed_at = reconciled_at
        row.reconciled_at = reconciled_at
    if rows and commit:
        session.commit()
    return len(rows)


def resolve_unknown_provider_call(
    session: Session,
    *,
    api_call_id: int,
    outcome: str,
    actual_cost_usd: float,
    resolution_note: str,
    now: datetime | None = None,
) -> ApiCallORM:
    if outcome not in {"billed", "not_billed"}:
        raise ValueError("outcome must be 'billed' or 'not_billed'.")
    if actual_cost_usd < 0:
        raise ValueError("actual_cost_usd cannot be negative.")
    if not resolution_note.strip():
        raise ValueError("An auditable resolution note is required.")
    resolved_at = now or datetime.now(UTC)
    row = session.get(ApiCallORM, api_call_id, with_for_update=True)
    if (
        row is None
        or row.attempt_state != "provider_outcome_unknown"
        or row.reservation_state not in {"none", "reconciled_unknown"}
    ):
        raise ValueError("API call is not awaiting provider-outcome reconciliation.")
    estimated_cost = row.reservation_estimated_cost_usd
    usage_class = row.reservation_usage_class or "testing"
    if row.reservation_usage_date is not None:
        for endpoint in ("", row.endpoint):
            bucket = _locked_bucket(
                session,
                usage_date=row.reservation_usage_date,
                usage_class=usage_class,
                provider=row.provider,
                endpoint=endpoint,
            )
            bucket.unreconciled_spend_usd = max(
                0.0,
                bucket.unreconciled_spend_usd - estimated_cost,
            )
            bucket.spend_usd += actual_cost_usd
    row.actual_cost_usd = actual_cost_usd
    row.attempt_state = "reconciled"
    row.provider_outcome = outcome
    row.reservation_state = "finalized"
    row.status = "completed" if outcome == "billed" else "provider_confirmed_not_billed"
    row.reconciled_at = resolved_at
    row.completed_at = resolved_at
    row.error_summary = f"{row.error_summary or ''}\nResolution: {resolution_note}".strip()
    session.commit()
    return row


def record_unexpected_call(
    session: Session,
    *,
    provider: str,
    environment: str,
    endpoint: str,
    now: datetime | None = None,
) -> None:
    called_at = now or datetime.now(UTC)
    usage_class = _usage_class(environment)
    for bucket_endpoint in ("", endpoint):
        bucket = _locked_bucket(
            session,
            usage_date=called_at.date(),
            usage_class=usage_class,
            provider=provider,
            endpoint=bucket_endpoint,
        )
        bucket.unexpected_call_count += 1
    session.commit()


def daily_usage(
    session: Session,
    *,
    provider: str,
    usage_date: date,
) -> dict[str, object]:
    rows = session.scalars(
        select(ProviderDailyUsageORM).where(
            ProviderDailyUsageORM.provider == provider,
            ProviderDailyUsageORM.usage_date == usage_date,
        )
    ).all()
    summaries = {row.usage_class: row for row in rows if row.endpoint == ""}
    endpoints = [row for row in rows if row.endpoint]
    production = summaries.get("production")
    testing = summaries.get("testing")
    return {
        "production_requests_today": production.request_count if production else 0,
        "production_spend_today": production.spend_usd if production else 0.0,
        "production_unreconciled_spend_today": (
            production.unreconciled_spend_usd if production else 0.0
        ),
        "testing_requests_today": testing.request_count if testing else 0,
        "testing_spend_today": testing.spend_usd if testing else 0.0,
        "testing_unreconciled_spend_today": (
            testing.unreconciled_spend_usd if testing else 0.0
        ),
        "cache_misses": sum(row.cache_miss_count for row in summaries.values()),
        "unexpected_calls": sum(row.unexpected_call_count for row in summaries.values()),
        "abnormal_cost_calls": sum(row.abnormal_cost_count for row in summaries.values()),
        "provider_endpoint_spend": {
            f"{row.usage_class}:{row.endpoint}": (
                row.spend_usd + row.unreconciled_spend_usd
            )
            for row in endpoints
        },
    }


def evaluate_alerts(
    session: Session,
    *,
    settings: Settings,
    provider: str,
    usage_date: date,
    now: datetime | None = None,
) -> list[str]:
    checked_at = now or datetime.now(UTC)
    rows = session.scalars(
        select(ProviderDailyUsageORM).where(
            ProviderDailyUsageORM.provider == provider,
            ProviderDailyUsageORM.usage_date == usage_date,
            ProviderDailyUsageORM.endpoint == "",
        )
    ).all()
    alerts: list[str] = []
    for row in rows:
        limit = (
            settings.production_daily_spend_usd
            if row.usage_class == "production"
            else settings.testing_daily_spend_usd
        )
        spend = row.spend_usd + row.reserved_spend_usd + row.unreconciled_spend_usd
        if row.unexpected_call_count:
            alerts.append("unexpected_paid_call")
        if row.abnormal_cost_count:
            alerts.append("abnormal_endpoint_cost")
        if row.cache_miss_count >= settings.unexpected_call_breaker_threshold:
            alerts.append("repeated_cache_misses")
        if row.usage_class == "testing" and row.spend_usd > 0:
            alerts.append("paid_testing_response")
        if limit > 0:
            ratio = spend / limit
            if ratio >= 1:
                alerts.append("daily_spend_100_percent")
            elif ratio >= 0.8:
                alerts.append("daily_spend_80_percent")
            elif ratio >= 0.5:
                alerts.append("daily_spend_50_percent")
        if row.request_count >= settings.circuit_breaker_minimum_requests:
            if (
                row.provider_failure_count / max(1, row.request_count)
                > settings.provider_failure_rate_threshold
            ):
                alerts.append("high_provider_error_rate")
            if (
                row.schema_drift_count / max(1, row.request_count)
                > settings.schema_drift_rate_threshold
            ):
                alerts.append("schema_drift_rate")
    stale_cutoff = checked_at - timedelta(seconds=settings.scan_worker_stale_after_seconds)
    if session.scalar(
        select(ScanRunORM.id)
        .where(
            ScanRunORM.status == "running",
            or_(ScanRunORM.heartbeat_at.is_(None), ScanRunORM.heartbeat_at < stale_cutoff),
        )
        .limit(1)
    ):
        alerts.append("stale_worker")
    long_running_cutoff = checked_at - timedelta(
        seconds=settings.scan_worker_long_running_seconds
    )
    if session.scalar(
        select(ScanRunORM.id)
        .where(
            ScanRunORM.status == "running",
            ScanRunORM.started_at.is_not(None),
            ScanRunORM.started_at < long_running_cutoff,
        )
        .limit(1)
    ):
        alerts.append("long_running_scan")
    if session.scalar(select(ScanRunORM.id).where(ScanRunORM.status == "quarantined").limit(1)):
        alerts.append("poison_job")
    day_start = datetime.combine(usage_date, datetime.min.time(), UTC)
    day_end = day_start + timedelta(days=1)
    if session.scalar(
        select(ApiCallORM.id)
        .where(
            ApiCallORM.provider == provider,
            ApiCallORM.status == "completed",
            ApiCallORM.actual_cost_usd > 0,
            ApiCallORM.planned_request_id.is_(None),
            ApiCallORM.completed_at >= day_start,
            ApiCallORM.completed_at < day_end,
        )
        .limit(1)
    ):
        alerts.append("paid_call_without_plan")
    if session.scalar(
        select(ApiCallORM.id)
        .where(
            ApiCallORM.provider == provider,
            ApiCallORM.status == "provider_outcome_unknown",
        )
        .limit(1)
    ):
        alerts.append("provider_outcome_unknown")
    latest_reconciliation = session.scalars(
        select(BillingReconciliationORM)
        .where(BillingReconciliationORM.provider == provider)
        .order_by(BillingReconciliationORM.reconciled_at.desc())
        .limit(1)
    ).first()
    if latest_reconciliation is not None and latest_reconciliation.status != "clean":
        alerts.append("provider_internal_cost_mismatch")
    return sorted(set(alerts))


def _assert_switches(settings: Settings, *, environment: str, scan_profile: str) -> None:
    if not settings.allow_live_api_calls:
        raise CircuitOpenError("ALLOW_LIVE_API_CALLS is false.")
    if settings.paid_call_kill_switch:
        raise CircuitOpenError("PAID_CALL_KILL_SWITCH is active.")
    if environment == "production" and not settings.allow_production_dataforseo:
        raise CircuitOpenError("ALLOW_PRODUCTION_DATAFORSEO is false.")
    if scan_profile == "full" and not settings.allow_full_scans:
        raise CircuitOpenError("ALLOW_FULL_SCANS is false.")


def _assert_durable_breakers(
    session: Session,
    *,
    settings: Settings,
    summary: ProviderDailyUsageORM,
    environment: str,
    provider: str,
    adapter_version: str,
    usage_class: str,
    estimated_cost_usd: float,
    require_current_qualification: bool,
    now: datetime,
) -> None:
    projected_spend = (
        summary.spend_usd
        + summary.reserved_spend_usd
        + summary.unreconciled_spend_usd
        + estimated_cost_usd
    )
    if usage_class == "production":
        if summary.request_count >= settings.production_daily_request_limit:
            raise CircuitOpenError("Production daily request limit reached.")
        if projected_spend > settings.production_daily_spend_usd:
            raise CircuitOpenError("Production daily spend limit reached.")
    elif projected_spend > settings.testing_daily_spend_usd:
        raise CircuitOpenError("Testing daily spend limit reached.")
    if summary.unexpected_call_count >= settings.unexpected_call_breaker_threshold:
        raise CircuitOpenError("Repeated unexpected calls opened the circuit breaker.")
    if summary.request_count >= settings.circuit_breaker_minimum_requests:
        failure_rate = summary.provider_failure_count / max(1, summary.request_count)
        drift_rate = summary.schema_drift_count / max(1, summary.request_count)
        if failure_rate > settings.provider_failure_rate_threshold:
            raise CircuitOpenError("Provider failure-rate threshold exceeded.")
        if drift_rate > settings.schema_drift_rate_threshold:
            raise CircuitOpenError("Provider schema-drift threshold exceeded.")
    if environment != "production" or usage_class != "production":
        return
    if require_current_qualification:
        qualification = current_qualification(
            session,
            provider=provider,
            environment=environment,
            adapter_version=adapter_version,
            now=now,
        )
        if qualification is None:
            raise CircuitOpenError(
                "Production qualification is missing, stale, failing, or for another "
                "adapter version."
            )
    latest_reconciliation = session.scalars(
        select(BillingReconciliationORM)
        .where(
            BillingReconciliationORM.provider == provider,
            BillingReconciliationORM.environment == environment,
        )
        .order_by(BillingReconciliationORM.reconciled_at.desc())
        .limit(1)
    ).first()
    reconciliation_cutoff = now - timedelta(hours=settings.billing_reconciliation_max_age_hours)
    if latest_reconciliation is not None and (
        latest_reconciliation.status != "clean"
        or _aware_utc(latest_reconciliation.reconciled_at) < reconciliation_cutoff
    ):
        raise CircuitOpenError("Provider billing is not currently reconciled.")
    if latest_reconciliation is None:
        oldest_paid_call = session.scalar(
            select(func.min(ApiCallORM.completed_at)).where(
                ApiCallORM.provider == provider,
                ApiCallORM.status == "completed",
                ApiCallORM.actual_cost_usd > 0,
                ApiCallORM.completed_at.is_not(None),
            )
        )
        if oldest_paid_call is not None and _aware_utc(oldest_paid_call) < reconciliation_cutoff:
            raise CircuitOpenError("Provider billing is not currently reconciled.")


def _usage_class(environment: str) -> str:
    return "production" if environment.strip().lower() == "production" else "testing"


def _locked_bucket(
    session: Session,
    *,
    usage_date: date,
    usage_class: str,
    provider: str,
    endpoint: str,
) -> ProviderDailyUsageORM:
    values = {
        "usage_date": usage_date,
        "usage_class": usage_class,
        "provider": provider,
        "endpoint": endpoint,
    }
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        session.execute(pg_insert(ProviderDailyUsageORM).values(**values).on_conflict_do_nothing())
    elif dialect == "sqlite":
        session.execute(
            sqlite_insert(ProviderDailyUsageORM).values(**values).on_conflict_do_nothing()
        )
    else:
        existing = session.scalar(select(ProviderDailyUsageORM.id).filter_by(**values))
        if existing is None:
            session.add(ProviderDailyUsageORM(**values))
            session.flush()
    bucket = session.scalar(select(ProviderDailyUsageORM).filter_by(**values).with_for_update())
    if bucket is None:
        raise RuntimeError("Could not create the durable provider-usage bucket.")
    return bucket


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _reconcile_attempt_reservation(
    session: Session,
    api_call: ApiCallORM,
    *,
    unknown: bool,
    reconciled_at: datetime,
) -> None:
    if api_call.reservation_state != "reserved" or api_call.reservation_usage_date is None:
        return
    usage_class = api_call.reservation_usage_class or "testing"
    estimated_cost = api_call.reservation_estimated_cost_usd
    for endpoint in ("", api_call.endpoint):
        bucket = _locked_bucket(
            session,
            usage_date=api_call.reservation_usage_date,
            usage_class=usage_class,
            provider=api_call.provider,
            endpoint=endpoint,
        )
        bucket.reserved_spend_usd = max(0.0, bucket.reserved_spend_usd - estimated_cost)
        if unknown:
            bucket.unreconciled_spend_usd += estimated_cost
    api_call.reservation_state = "reconciled_unknown" if unknown else "released"
    api_call.reconciled_at = reconciled_at
