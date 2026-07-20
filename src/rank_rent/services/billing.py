from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import ApiCallORM, BillingReconciliationORM


@dataclass(frozen=True)
class ProviderCharge:
    provider_request_id: str | None
    provider_task_id: str | None
    endpoint: str
    cost_usd: float
    billed_at: datetime


def reconcile_billing_csv(
    session: Session,
    csv_path: Path,
    *,
    provider: str,
    environment: str,
    tolerance_usd: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    charges = _read_charges(csv_path)
    if not charges:
        raise ValueError("Billing CSV contains no charge rows.")
    period_start = min(charge.billed_at.date() for charge in charges)
    period_end = max(charge.billed_at.date() for charge in charges)
    calls = list(
        session.scalars(
            select(ApiCallORM).where(
                ApiCallORM.provider == provider,
                ApiCallORM.status == "completed",
                ApiCallORM.completed_at >= datetime.combine(period_start, datetime.min.time(), UTC),
                ApiCallORM.completed_at
                < datetime.combine(period_end, datetime.min.time(), UTC) + timedelta(days=1),
            )
        ).all()
    )
    unmatched_calls = {call.id: call for call in calls}
    unmatched_charges: list[ProviderCharge] = []
    for charge in charges:
        match = next(
            (call for call in unmatched_calls.values() if _matches(call, charge)),
            None,
        )
        if match is None:
            unmatched_charges.append(charge)
        else:
            unmatched_calls.pop(match.id, None)
    internal_cost = round(sum(call.actual_cost_usd for call in calls), 6)
    provider_cost = round(sum(charge.cost_usd for charge in charges), 6)
    difference = round(provider_cost - internal_cost, 6)
    clean = not unmatched_charges and not unmatched_calls and abs(difference) <= tolerance_usd
    reconciled_at = now or datetime.now(UTC)
    row = BillingReconciliationORM(
        provider=provider,
        environment=environment,
        period_start=period_start,
        period_end=period_end,
        reconciled_at=reconciled_at,
        status="clean" if clean else "mismatch",
        internal_call_count=len(calls),
        provider_call_count=len(charges),
        internal_cost_usd=internal_cost,
        provider_cost_usd=provider_cost,
        unmatched_provider_charges=[_charge_payload(item) for item in unmatched_charges],
        unmatched_internal_calls=[_call_payload(item) for item in unmatched_calls.values()],
        difference_usd=difference,
        source_filename=csv_path.name,
    )
    session.add(row)
    session.commit()
    return {
        "status": row.status,
        "internal_call_count": row.internal_call_count,
        "provider_call_count": row.provider_call_count,
        "internal_cost": row.internal_cost_usd,
        "provider_cost": row.provider_cost_usd,
        "unmatched_provider_charges": row.unmatched_provider_charges,
        "unmatched_internal_calls": row.unmatched_internal_calls,
        "difference": row.difference_usd,
    }


def _read_charges(path: Path) -> list[ProviderCharge]:
    charges: list[ProviderCharge] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            billed_at = datetime.fromisoformat(
                str(row.get("billed_at") or "").replace("Z", "+00:00")
            )
            if billed_at.tzinfo is None:
                billed_at = billed_at.replace(tzinfo=UTC)
            charges.append(
                ProviderCharge(
                    provider_request_id=_clean(row.get("provider_request_id")),
                    provider_task_id=_clean(row.get("provider_task_id")),
                    endpoint=str(row.get("endpoint") or ""),
                    cost_usd=float(row.get("cost_usd") or 0),
                    billed_at=billed_at.astimezone(UTC),
                )
            )
    return charges


def _matches(call: ApiCallORM, charge: ProviderCharge) -> bool:
    if charge.provider_request_id and call.provider_request_id == charge.provider_request_id:
        return True
    return bool(charge.provider_task_id and call.provider_task_id == charge.provider_task_id)


def _charge_payload(charge: ProviderCharge) -> dict[str, Any]:
    return {
        "provider_request_id": charge.provider_request_id,
        "provider_task_id": charge.provider_task_id,
        "endpoint": charge.endpoint,
        "cost_usd": charge.cost_usd,
        "billed_at": charge.billed_at.isoformat(),
    }


def _call_payload(call: ApiCallORM) -> dict[str, Any]:
    return {
        "api_call_id": call.id,
        "provider_request_id": call.provider_request_id,
        "provider_task_id": call.provider_task_id,
        "endpoint": call.endpoint,
        "cost_usd": call.actual_cost_usd,
    }


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
