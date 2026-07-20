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
    now: datetime | None = None,
) -> UsageReservation:
    called_at = now or datetime.now(UTC)
    usage_class = _usage_class(environment)
    _assert_switches(settings, environment=environment, scan_profile=scan_profile)
    if estimated_cost_usd > settings.single_call_abnormal_cost_usd:
        raise CircuitOpenError(
            f"Estimated endpoint cost ${estimated_cost_usd:.4f} exceeds the abnormal-call limit "
            f"${settings.single_call_abnormal_cost_usd:.4f}."
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
    session.commit()
    return UsageReservation(
        usage_date=called_at.date(),
        usage_class=usage_class,
        provider=provider,
        endpoint=endpoint,
        estimated_cost_usd=estimated_cost_usd,
    )


def finish_provider_call(
    session: Session,
    reservation: UsageReservation,
    *,
    actual_cost_usd: float,
    failed: bool = False,
    schema_drift: bool = False,
    abnormal_cost: bool = False,
) -> None:
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
        bucket.spend_usd += max(0.0, actual_cost_usd)
        if failed:
            bucket.provider_failure_count += 1
        if schema_drift:
            bucket.schema_drift_count += 1
        if abnormal_cost:
            bucket.abnormal_cost_count += 1
    session.commit()


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
        "testing_requests_today": testing.request_count if testing else 0,
        "testing_spend_today": testing.spend_usd if testing else 0.0,
        "cache_misses": sum(row.cache_miss_count for row in summaries.values()),
        "unexpected_calls": sum(row.unexpected_call_count for row in summaries.values()),
        "abnormal_cost_calls": sum(row.abnormal_cost_count for row in summaries.values()),
        "provider_endpoint_spend": {
            f"{row.usage_class}:{row.endpoint}": row.spend_usd for row in endpoints
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
        spend = row.spend_usd + row.reserved_spend_usd
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
    projected_spend = summary.spend_usd + summary.reserved_spend_usd + estimated_cost_usd
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
