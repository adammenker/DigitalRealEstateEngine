from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import ProviderQualificationORM

DATAFORSEO_ADAPTER_VERSION = "dataforseo-v3-workstream-d-2"
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


class QualificationExecutor(Protocol):
    async def execute_check(self, check_name: str) -> dict[str, Any]: ...


async def execute_qualification(
    session: Session,
    *,
    provider: str,
    environment: str,
    adapter_version: str,
    executor: QualificationExecutor,
    ttl_hours: int,
    executed_by: str,
    notes: str = "",
    now: datetime | None = None,
) -> ProviderQualificationORM:
    evidence: dict[str, dict[str, Any]] = {}
    for check_name in REQUIRED_QUALIFICATION_CHECKS:
        started_at = datetime.now(UTC)
        try:
            detail = await executor.execute_check(check_name)
            passed = detail.get("passed") is True
            error: dict[str, str] | None = None
        except Exception as exc:
            detail = {}
            passed = False
            error = {"type": type(exc).__name__, "summary": str(exc)}
        evidence[check_name] = {
            "passed": passed,
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "evidence": detail,
            "error": error,
        }
    return record_executed_qualification(
        session,
        provider=provider,
        environment=environment,
        adapter_version=adapter_version,
        checks=evidence,
        ttl_hours=ttl_hours,
        executed_by=executed_by,
        notes=notes,
        now=now,
    )


def record_executed_qualification(
    session: Session,
    *,
    provider: str,
    environment: str,
    adapter_version: str,
    checks: dict[str, Any],
    ttl_hours: int,
    executed_by: str,
    notes: str = "",
    now: datetime | None = None,
) -> ProviderQualificationORM:
    qualified_at = now or datetime.now(UTC)
    normalized = _normalize_executed_checks(checks)
    status = "passed" if all(item["passed"] for item in normalized.values()) else "failed"
    evidence_sha256 = _evidence_hash(
        {
            "provider": provider,
            "environment": environment,
            "adapter_version": adapter_version,
            "checks": normalized,
        }
    )
    row = ProviderQualificationORM(
        provider=provider,
        environment=environment,
        adapter_version=adapter_version,
        status=status,
        qualified_at=qualified_at,
        expires_at=qualified_at + timedelta(hours=ttl_hours),
        checks=normalized,
        notes=notes,
        execution_method="executable_runner",
        gate_eligible=status == "passed",
        evidence_sha256=evidence_sha256,
        executed_by=executed_by,
        override_reason="",
    )
    session.add(row)
    session.commit()
    return row


def record_qualification(
    session: Session,
    *,
    provider: str,
    environment: str,
    adapter_version: str,
    checks: dict[str, Any],
    ttl_hours: int,
    notes: str = "",
    executed_by: str = "manual-import",
    override_reason: str = "",
    now: datetime | None = None,
) -> ProviderQualificationORM:
    """Record an audited manual import that can never unlock production paid calls."""
    if not override_reason.strip():
        raise ValueError("Manual qualification imports require an auditable override reason.")
    qualified_at = now or datetime.now(UTC)
    normalized = {
        name: {
            "passed": _passed(checks.get(name)),
            "detail": checks.get(name),
        }
        for name in REQUIRED_QUALIFICATION_CHECKS
    }
    status = "passed" if all(item["passed"] for item in normalized.values()) else "failed"
    row = ProviderQualificationORM(
        provider=provider,
        environment=environment,
        adapter_version=adapter_version,
        status=status,
        qualified_at=qualified_at,
        expires_at=qualified_at + timedelta(hours=ttl_hours),
        checks=normalized,
        notes=notes,
        execution_method="manual_import",
        gate_eligible=False,
        evidence_sha256=_evidence_hash(normalized),
        executed_by=executed_by,
        override_reason=override_reason,
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
            ProviderQualificationORM.execution_method == "executable_runner",
        )
        .order_by(
            ProviderQualificationORM.qualified_at.desc(),
            ProviderQualificationORM.id.desc(),
        )
        .limit(1)
    ).first()
    if (
        latest is None
        or latest.status != "passed"
        or not latest.gate_eligible
        or _aware_utc(latest.expires_at) <= checked_at
    ):
        return None
    return latest


def qualification_executor(
    handler: Callable[[str], Awaitable[dict[str, Any]]],
) -> QualificationExecutor:
    class CallableExecutor:
        async def execute_check(self, check_name: str) -> dict[str, Any]:
            return await handler(check_name)

    return CallableExecutor()


def _normalize_executed_checks(checks: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_QUALIFICATION_CHECKS:
        value = checks.get(name)
        if not isinstance(value, dict):
            normalized[name] = {"passed": False, "error": "Missing executable evidence."}
            continue
        normalized[name] = {
            **value,
            "passed": value.get("passed") is True,
        }
    return normalized


def _evidence_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _passed(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("passed") is True
    return value is True


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
