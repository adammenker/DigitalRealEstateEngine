from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from rank_rent.db.base import WorkerSessionLocal
from rank_rent.lead_routing.adapters import RetryableDeliveryError
from rank_rent.lead_routing.interfaces import DeliveryAdapter, OperatorAlertAdapter
from rank_rent.lead_routing.models import (
    DeliveryChannel,
    DeliveryRequest,
    DeliveryResult,
    DeliveryStatus,
    LeadStatus,
    ProviderAssignmentStatus,
)
from rank_rent.lead_routing.orm import (
    LeadEventORM,
    LeadORM,
    ProviderAssignmentORM,
    ProviderDeliveryORM,
    RoutingAttemptORM,
)
from rank_rent.lead_routing.privacy import masked_destination

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]
CLAIMABLE_DELIVERY_STATUSES = {
    DeliveryStatus.pending.value,
    DeliveryStatus.retrying.value,
}
ACTIVE_DELIVERY_STATUSES = {
    DeliveryStatus.leased.value,
    DeliveryStatus.delivering.value,
}
NONTERMINAL_DELIVERY_STATUSES = CLAIMABLE_DELIVERY_STATUSES | ACTIVE_DELIVERY_STATUSES


@dataclass(frozen=True)
class DeliveryLease:
    delivery_id: str
    worker_id: str
    lease_token: str


def build_delivery_worker_id(slot: int | None = None) -> str:
    suffix = f":{slot}" if slot is not None else ""
    return f"{socket.gethostname()}:{suffix}{uuid.uuid4().hex[:12]}"


def claim_next_delivery(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: float = 30.0,
    now: datetime | None = None,
) -> DeliveryLease | None:
    claimed_at = now or datetime.now(UTC)
    delivery_ids = session.scalars(
        select(ProviderDeliveryORM.id)
        .where(
            ProviderDeliveryORM.status.in_(CLAIMABLE_DELIVERY_STATUSES),
            or_(
                ProviderDeliveryORM.next_attempt_at.is_(None),
                ProviderDeliveryORM.next_attempt_at <= claimed_at,
            ),
        )
        .order_by(ProviderDeliveryORM.next_attempt_at, ProviderDeliveryORM.id)
        .limit(10)
    ).all()
    for delivery_id in delivery_ids:
        lease_token = uuid.uuid4().hex
        claimed = session.execute(
            update(ProviderDeliveryORM)
            .where(
                ProviderDeliveryORM.id == delivery_id,
                ProviderDeliveryORM.status.in_(CLAIMABLE_DELIVERY_STATUSES),
                or_(
                    ProviderDeliveryORM.next_attempt_at.is_(None),
                    ProviderDeliveryORM.next_attempt_at <= claimed_at,
                ),
            )
            .values(
                status=DeliveryStatus.leased.value,
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
        return DeliveryLease(
            delivery_id=delivery_id,
            worker_id=worker_id,
            lease_token=lease_token,
        )
    return None


def recover_stale_deliveries(
    session: Session,
    *,
    stale_after_seconds: float,
    now: datetime | None = None,
) -> int:
    recovered_at = now or datetime.now(UTC)
    cutoff = recovered_at - timedelta(seconds=stale_after_seconds)
    rows = session.scalars(
        select(ProviderDeliveryORM)
        .where(
            ProviderDeliveryORM.status.in_(ACTIVE_DELIVERY_STATUSES),
            or_(
                ProviderDeliveryORM.lease_expires_at < recovered_at,
                ProviderDeliveryORM.lease_expires_at.is_(None),
                ProviderDeliveryORM.heartbeat_at < cutoff,
                ProviderDeliveryORM.heartbeat_at.is_(None),
            ),
        )
        .order_by(ProviderDeliveryORM.id)
        .with_for_update(skip_locked=True)
    ).all()
    for delivery in rows:
        if delivery.status == DeliveryStatus.delivering.value:
            delivery.status = DeliveryStatus.outcome_unknown.value
            delivery.last_error_code = "worker_lost_during_provider_call"
            delivery.last_error_summary = (
                "The worker lease expired after provider delivery began. "
                "Automatic resend is blocked to prevent duplicate provider contact."
            )
            delivery.completed_at = recovered_at
            attempt = session.scalar(
                select(RoutingAttemptORM)
                .where(
                    RoutingAttemptORM.delivery_key == delivery.delivery_key,
                    RoutingAttemptORM.status == "running",
                )
                .order_by(RoutingAttemptORM.attempt_number.desc())
            )
            if attempt is not None:
                attempt.status = "outcome_unknown"
                attempt.error_code = delivery.last_error_code
                attempt.error_summary = delivery.last_error_summary
                attempt.completed_at = recovered_at
        else:
            delivery.status = DeliveryStatus.retrying.value
            delivery.next_attempt_at = recovered_at
            delivery.last_error_code = "worker_lost_before_provider_call"
            delivery.last_error_summary = (
                "The worker lease expired before provider delivery began; the job was requeued."
            )
        _clear_lease(delivery)
        _refresh_lead_state(session, delivery.lead_id, recovered_at)
    if rows:
        session.commit()
    return len(rows)


async def run_delivery_by_id(
    lease: DeliveryLease,
    *,
    adapters: dict[DeliveryChannel, DeliveryAdapter],
    alert_adapter: OperatorAlertAdapter,
    session_factory: SessionFactory | None = None,
    heartbeat_seconds: float = 5.0,
    lease_seconds: float = 30.0,
    retry_base_seconds: float = 30.0,
    retry_max_seconds: float = 3600.0,
) -> None:
    factory = session_factory or WorkerSessionLocal
    request: DeliveryRequest | None = None
    adapter: DeliveryAdapter | None = None
    attempt_number: int | None = None
    failure_to_alert: tuple[str, str, str] | None = None
    with factory() as session:
        delivery = session.get(ProviderDeliveryORM, lease.delivery_id)
        if delivery is None or not _owns(delivery, lease):
            return
        lead = session.get(LeadORM, delivery.lead_id)
        assignment = session.get(ProviderAssignmentORM, delivery.provider_assignment_id)
        channel = DeliveryChannel(delivery.channel)
        adapter = adapters.get(channel)
        destination = _assignment_destination(assignment, channel)
        error_code: str | None = None
        if lead is None:
            error_code = "lead_not_found"
        elif assignment is None:
            error_code = "provider_assignment_not_found"
        elif assignment.status != ProviderAssignmentStatus.active.value:
            error_code = "provider_assignment_not_active"
        elif lead.provider_assignment_id != assignment.id:
            error_code = "lead_assignment_mismatch"
        elif adapter is None or adapter.name != delivery.adapter_name:
            error_code = "delivery_adapter_unavailable"
        elif destination is None:
            error_code = "assignment_destination_unavailable"
        elif masked_destination(destination) != delivery.destination_reference:
            error_code = "assignment_destination_changed"
        if error_code is not None:
            failure_to_alert = _fail_delivery(
                session,
                delivery,
                error_code=error_code,
                summary="Delivery failed validation before the provider adapter was called.",
                at=datetime.now(UTC),
            )
            session.commit()
        else:
            assert lead is not None and assignment is not None and destination is not None
            attempt_number = delivery.attempt_count + 1
            delivery.attempt_count = attempt_number
            delivery.status = DeliveryStatus.delivering.value
            created_attempt = RoutingAttemptORM(
                lead_id=lead.id,
                provider_assignment_id=assignment.id,
                channel=channel.value,
                delivery_key=delivery.delivery_key,
                attempt_number=attempt_number,
                status="running",
                started_at=datetime.now(UTC),
            )
            session.add(created_attempt)
            request = DeliveryRequest(
                delivery_key=delivery.delivery_key,
                property_id=lead.property_id,
                lead_id=lead.id,
                provider_assignment_id=assignment.id,
                channel=channel,
                destination=destination,
                contact_name=lead.name,
                contact_email=lead.email,
                contact_phone=lead.phone,
                message=lead.message,
            )
            session.commit()
    if failure_to_alert is not None:
        await _alert_failure(alert_adapter, *failure_to_alert)
        return
    if request is None or adapter is None or attempt_number is None:
        return

    stop_heartbeat = asyncio.Event()
    heartbeat = asyncio.create_task(
        _heartbeat_delivery(
            lease,
            stop_event=stop_heartbeat,
            session_factory=factory,
            heartbeat_seconds=heartbeat_seconds,
            lease_seconds=lease_seconds,
        )
    )
    result: DeliveryResult | None = None
    error: Exception | None = None
    try:
        result = await adapter.deliver(request)
    except Exception as exc:
        error = exc
    finally:
        stop_heartbeat.set()
        with suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(heartbeat, timeout=max(1.0, heartbeat_seconds + 1.0))

    failure_to_alert = None
    with factory() as session:
        delivery = session.get(ProviderDeliveryORM, lease.delivery_id)
        if delivery is None or not _owns(delivery, lease):
            return
        stored_attempt = session.scalar(
            select(RoutingAttemptORM).where(
                RoutingAttemptORM.delivery_key == delivery.delivery_key,
                RoutingAttemptORM.attempt_number == attempt_number,
            )
        )
        if stored_attempt is None:
            return
        completed_at = datetime.now(UTC)
        if isinstance(error, RetryableDeliveryError):
            failure_to_alert = _retry_or_fail(
                session,
                delivery,
                stored_attempt,
                at=completed_at,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            )
        elif error is not None:
            stored_attempt.status = "permanent_failure"
            stored_attempt.error_code = "adapter_failure"
            stored_attempt.error_summary = "Delivery adapter failed with a permanent error."
            stored_attempt.completed_at = completed_at
            failure_to_alert = _fail_delivery(
                session,
                delivery,
                error_code=stored_attempt.error_code,
                summary=stored_attempt.error_summary,
                at=completed_at,
            )
        elif result is not None and result.accepted:
            stored_attempt.status = "succeeded"
            stored_attempt.completed_at = completed_at
            delivery.provider_message_id = result.provider_message_id
            delivery.status = DeliveryStatus.delivered.value
            delivery.delivered_at = completed_at
            delivery.completed_at = completed_at
            delivery.last_error_code = None
            delivery.last_error_summary = None
            _clear_lease(delivery)
            _refresh_lead_state(session, delivery.lead_id, completed_at)
        elif result is not None and result.retryable:
            failure_to_alert = _retry_or_fail(
                session,
                delivery,
                stored_attempt,
                at=completed_at,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            )
        else:
            stored_attempt.status = "rejected"
            stored_attempt.error_code = "provider_rejected_delivery"
            stored_attempt.error_summary = "The provider adapter rejected delivery."
            stored_attempt.completed_at = completed_at
            failure_to_alert = _fail_delivery(
                session,
                delivery,
                error_code=stored_attempt.error_code,
                summary=stored_attempt.error_summary,
                at=completed_at,
            )
        session.commit()
    if failure_to_alert is not None:
        await _alert_failure(alert_adapter, *failure_to_alert)


async def lead_delivery_worker_loop(
    stop_event: asyncio.Event,
    *,
    adapters: dict[DeliveryChannel, DeliveryAdapter],
    alert_adapter: OperatorAlertAdapter,
    session_factory: SessionFactory | None = None,
    worker_id: str | None = None,
    poll_seconds: float = 1.0,
    heartbeat_seconds: float = 5.0,
    stale_after_seconds: float = 30.0,
    retry_base_seconds: float = 30.0,
    retry_max_seconds: float = 3600.0,
) -> None:
    factory = session_factory or WorkerSessionLocal
    active_worker_id = worker_id or build_delivery_worker_id()
    while not stop_event.is_set():
        with factory() as session:
            recover_stale_deliveries(
                session,
                stale_after_seconds=stale_after_seconds,
            )
            lease = claim_next_delivery(
                session,
                worker_id=active_worker_id,
                lease_seconds=stale_after_seconds,
            )
        if lease is not None:
            await run_delivery_by_id(
                lease,
                adapters=adapters,
                alert_adapter=alert_adapter,
                session_factory=factory,
                heartbeat_seconds=heartbeat_seconds,
                lease_seconds=stale_after_seconds,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            )
            continue
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)


async def run_lead_delivery_runtime(
    stop_event: asyncio.Event,
    *,
    concurrency: int,
    adapters: dict[DeliveryChannel, DeliveryAdapter],
    alert_adapter: OperatorAlertAdapter,
    session_factory: SessionFactory | None = None,
    poll_seconds: float = 1.0,
    heartbeat_seconds: float = 5.0,
    stale_after_seconds: float = 30.0,
    retry_base_seconds: float = 30.0,
    retry_max_seconds: float = 3600.0,
) -> None:
    tasks = [
        asyncio.create_task(
            lead_delivery_worker_loop(
                stop_event,
                adapters=adapters,
                alert_adapter=alert_adapter,
                session_factory=session_factory,
                worker_id=build_delivery_worker_id(slot),
                poll_seconds=poll_seconds,
                heartbeat_seconds=heartbeat_seconds,
                stale_after_seconds=stale_after_seconds,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            )
        )
        for slot in range(concurrency)
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        stop_event.set()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _heartbeat_delivery(
    lease: DeliveryLease,
    *,
    stop_event: asyncio.Event,
    session_factory: SessionFactory,
    heartbeat_seconds: float,
    lease_seconds: float,
) -> None:
    while not stop_event.is_set():
        now = datetime.now(UTC)
        with session_factory() as session:
            heartbeat = session.execute(
                update(ProviderDeliveryORM)
                .where(
                    ProviderDeliveryORM.id == lease.delivery_id,
                    ProviderDeliveryORM.status.in_(ACTIVE_DELIVERY_STATUSES),
                    ProviderDeliveryORM.worker_id == lease.worker_id,
                    ProviderDeliveryORM.lease_token == lease.lease_token,
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                )
            )
            session.commit()
            if not _rowcount(heartbeat):
                return
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=heartbeat_seconds)


def _retry_or_fail(
    session: Session,
    delivery: ProviderDeliveryORM,
    attempt: RoutingAttemptORM,
    *,
    at: datetime,
    retry_base_seconds: float,
    retry_max_seconds: float,
) -> tuple[str, str, str] | None:
    attempt.status = "retryable_failure"
    attempt.error_code = "retryable_adapter_failure"
    attempt.error_summary = "Delivery adapter reported a known transient failure."
    attempt.completed_at = at
    delivery.last_error_code = attempt.error_code
    delivery.last_error_summary = attempt.error_summary
    if delivery.attempt_count >= delivery.max_attempts:
        return _fail_delivery(
            session,
            delivery,
            error_code="delivery_attempts_exhausted",
            summary="Delivery exhausted its bounded retry allowance.",
            at=at,
        )
    delay = min(
        retry_max_seconds,
        retry_base_seconds * float(2 ** max(0, delivery.attempt_count - 1)),
    )
    delivery.status = DeliveryStatus.retrying.value
    delivery.next_attempt_at = at + timedelta(seconds=delay)
    _clear_lease(delivery)
    _refresh_lead_state(session, delivery.lead_id, at)
    return None


def _fail_delivery(
    session: Session,
    delivery: ProviderDeliveryORM,
    *,
    error_code: str,
    summary: str,
    at: datetime,
) -> tuple[str, str, str] | None:
    delivery.status = DeliveryStatus.failed.value
    delivery.last_error_code = error_code
    delivery.last_error_summary = summary
    delivery.completed_at = at
    _clear_lease(delivery)
    lead_failed = _refresh_lead_state(session, delivery.lead_id, at)
    if not lead_failed:
        return None
    lead = session.get(LeadORM, delivery.lead_id)
    if lead is None:
        return None
    return lead.property_id, lead.id, "all_deliveries_failed"


def _refresh_lead_state(session: Session, lead_id: str, at: datetime) -> bool:
    lead = session.get(LeadORM, lead_id)
    if lead is None:
        return False
    deliveries = list(
        session.scalars(
            select(ProviderDeliveryORM).where(ProviderDeliveryORM.lead_id == lead_id)
        )
    )
    if any(row.status in NONTERMINAL_DELIVERY_STATUSES for row in deliveries):
        lead.status = LeadStatus.routing.value
        return False
    if any(row.status == DeliveryStatus.delivered.value for row in deliveries):
        lead.status = LeadStatus.delivered.value
        _add_lead_event(
            session,
            lead_id=lead_id,
            event_type="provider_delivery",
            event_key=f"delivery:{lead_id}",
            occurred_at=at,
        )
        return False
    lead.status = LeadStatus.delivery_failed.value
    _add_lead_event(
        session,
        lead_id=lead_id,
        event_type="delivery_failed",
        event_key=f"delivery-failed:{lead_id}",
        occurred_at=at,
    )
    return True


def _add_lead_event(
    session: Session,
    *,
    lead_id: str,
    event_type: str,
    event_key: str,
    occurred_at: datetime,
) -> None:
    existing = session.scalar(
        select(LeadEventORM.id).where(
            LeadEventORM.lead_id == lead_id,
            LeadEventORM.event_key == event_key,
        )
    )
    if existing is not None:
        return
    session.add(
        LeadEventORM(
            lead_id=lead_id,
            event_type=event_type,
            event_key=event_key,
            truth_basis="observed",
            source_type="system",
            source_name="lead-delivery-worker",
            occurred_at=occurred_at,
            payload={},
        )
    )


def _assignment_destination(
    assignment: ProviderAssignmentORM | None,
    channel: DeliveryChannel,
) -> str | None:
    if assignment is None:
        return None
    if channel == DeliveryChannel.email:
        return assignment.destination_email
    return assignment.destination_phone


async def _alert_failure(
    adapter: OperatorAlertAdapter,
    property_id: str,
    lead_id: str,
    reason_code: str,
) -> None:
    try:
        await adapter.routing_failure(
            property_id=property_id,
            lead_id=lead_id,
            reason_code=reason_code,
        )
    except Exception:
        logger.exception("Operator alert failed for lead %s.", lead_id)


def _owns(delivery: ProviderDeliveryORM, lease: DeliveryLease) -> bool:
    return (
        delivery.status in ACTIVE_DELIVERY_STATUSES
        and delivery.worker_id == lease.worker_id
        and delivery.lease_token == lease.lease_token
    )


def _clear_lease(delivery: ProviderDeliveryORM) -> None:
    delivery.worker_id = None
    delivery.lease_token = None
    delivery.claimed_at = None
    delivery.heartbeat_at = None
    delivery.lease_expires_at = None


def _rowcount(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)
