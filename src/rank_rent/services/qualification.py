from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import ProviderQualificationORM

DATAFORSEO_ADAPTER_VERSION = "dataforseo-v3-workstream-d-1"
REQUIRED_QUALIFICATION_CHECKS = (
    "account_access",
    "location_lookup",
    "keyword_suggestions",
    "keyword_metrics",
    "serps",
    "serp_features",
    "backlinks",
    "business_listings",
    "partial_tasks",
    "rate_limits",
    "billing_errors",
    "authentication_errors",
    "schema_drift",
)


def record_qualification(
    session: Session,
    *,
    provider: str,
    environment: str,
    adapter_version: str,
    checks: dict[str, Any],
    ttl_hours: int,
    notes: str = "",
    now: datetime | None = None,
) -> ProviderQualificationORM:
    qualified_at = now or datetime.now(UTC)
    normalized = {name: _passed(checks.get(name)) for name in REQUIRED_QUALIFICATION_CHECKS}
    status = "passed" if all(normalized.values()) else "failed"
    row = ProviderQualificationORM(
        provider=provider,
        environment=environment,
        adapter_version=adapter_version,
        status=status,
        qualified_at=qualified_at,
        expires_at=qualified_at + timedelta(hours=ttl_hours),
        checks={
            name: {
                "passed": normalized[name],
                "detail": checks.get(name),
            }
            for name in REQUIRED_QUALIFICATION_CHECKS
        },
        notes=notes,
    )
    session.add(row)
    session.commit()
    return row


def current_qualification(
    session: Session,
    *,
    provider: str,
    environment: str,
    adapter_version: str,
    now: datetime | None = None,
) -> ProviderQualificationORM | None:
    checked_at = now or datetime.now(UTC)
    latest = session.scalars(
        select(ProviderQualificationORM)
        .where(
            ProviderQualificationORM.provider == provider,
            ProviderQualificationORM.environment == environment,
            ProviderQualificationORM.adapter_version == adapter_version,
        )
        .order_by(
            ProviderQualificationORM.qualified_at.desc(),
            ProviderQualificationORM.id.desc(),
        )
        .limit(1)
    ).first()
    if latest is None or latest.status != "passed" or _aware_utc(latest.expires_at) <= checked_at:
        return None
    return latest


def _passed(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("passed") is True
    return value is True


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
