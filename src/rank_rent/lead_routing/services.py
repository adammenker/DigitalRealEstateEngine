from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rank_rent.db.orm import OpportunityORM
from rank_rent.lead_routing.adapters import RetryableDeliveryError
from rank_rent.lead_routing.interfaces import (
    CallTrackingAdapter,
    DeliveryAdapter,
    OperatorAlertAdapter,
    RateLimiter,
    SpamAssessor,
)
from rank_rent.lead_routing.models import (
    AnalyticsEventInput,
    CallRouteRequest,
    DeliveryChannel,
    DeliveryStatus,
    LeadChannel,
    LeadForm,
    LeadRoutingPolicy,
    LeadStatus,
    LeadSubmissionResult,
    ProviderAssignmentStatus,
    RequestContext,
)
from rank_rent.lead_routing.orm import (
    AnalyticsEventORM,
    ConsentRecordORM,
    LeadEventORM,
    LeadORM,
    LeadOutcomeORM,
    PropertyRoutingProfileORM,
    ProviderAssignmentORM,
    ProviderDeliveryORM,
    SpamAssessmentORM,
)
from rank_rent.lead_routing.privacy import (
    masked_destination,
    redact_pii,
    stable_private_hash,
    subject_fingerprint,
)
from rank_rent.opportunity_review.services import (
    OpportunityReviewError,
    require_property_approval,
)

logger = logging.getLogger(__name__)


class LeadRoutingError(RuntimeError):
    pass


class RateLimitExceeded(LeadRoutingError):
    pass


class ProviderAssignmentError(LeadRoutingError):
    pass


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "candidate": {"pilot", "terminated"},
    "pilot": {"active", "paused", "terminated"},
    "active": {"paused", "terminated", "replaced"},
    "paused": {"active", "terminated", "replaced"},
    "terminated": set(),
    "replaced": set(),
}


class ProviderOperationsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_routing_profile(
        self,
        *,
        property_id: str,
        opportunity_id: int,
        public_tracking_number: str | None = None,
        public_contact_email: str | None = None,
        recording_approved: bool = False,
        recording_retention_days: int | None = None,
    ) -> PropertyRoutingProfileORM:
        existing = self.session.scalar(
            select(PropertyRoutingProfileORM).where(
                PropertyRoutingProfileORM.property_id == property_id
            )
        )
        if existing is not None:
            if existing.opportunity_id != opportunity_id:
                raise ProviderAssignmentError("property_opportunity_is_immutable")
            return existing
        try:
            require_property_approval(self.session, opportunity_id)
        except OpportunityReviewError as exc:
            raise ProviderAssignmentError(exc.code) from exc
        if recording_approved and recording_retention_days is None:
            raise ProviderAssignmentError("recording_requires_retention")
        profile = PropertyRoutingProfileORM(
            property_id=property_id,
            opportunity_id=opportunity_id,
            public_tracking_number=public_tracking_number,
            public_contact_email=public_contact_email,
            recording_approved=recording_approved,
            recording_retention_days=recording_retention_days,
        )
        self.session.add(profile)
        self.session.flush()
        return profile

    def create_assignment(
        self,
        *,
        property_id: str,
        public_business_name: str,
        provider_candidate_id: int | None = None,
        destination_phone: str | None = None,
        destination_email: str | None = None,
        coverage: dict[str, object] | None = None,
        response_expectation_minutes: int | None = None,
        lead_acceptance_required: bool = True,
    ) -> ProviderAssignmentORM:
        if destination_phone is None and destination_email is None:
            raise ProviderAssignmentError("assignment_requires_destination")
        if self._profile(property_id) is None:
            raise ProviderAssignmentError("routing_profile_not_found")
        assignment = ProviderAssignmentORM(
            property_id=property_id,
            provider_candidate_id=provider_candidate_id,
            status=ProviderAssignmentStatus.candidate.value,
            public_business_name=public_business_name,
            destination_phone=destination_phone,
            destination_email=destination_email,
            coverage=coverage or {},
            response_expectation_minutes=response_expectation_minutes,
            lead_acceptance_required=lead_acceptance_required,
        )
        self.session.add(assignment)
        self.session.flush()
        return assignment

    def transition(
        self,
        assignment_id: int,
        target: ProviderAssignmentStatus,
        *,
        reason: str | None = None,
        at: datetime | None = None,
    ) -> ProviderAssignmentORM:
        assignment = self.session.get(ProviderAssignmentORM, assignment_id)
        if assignment is None:
            raise ProviderAssignmentError("assignment_not_found")
        if target.value == assignment.status:
            return assignment
        if target.value not in _ALLOWED_TRANSITIONS[assignment.status]:
            raise ProviderAssignmentError(
                f"invalid_assignment_transition:{assignment.status}:{target.value}"
            )
        timestamp = at or datetime.now(UTC)
        if target == ProviderAssignmentStatus.active:
            active = self._active_assignment(assignment.property_id)
            if active is not None and active.id != assignment.id:
                raise ProviderAssignmentError("property_already_has_active_assignment")
            assignment.active_from = assignment.active_from or timestamp
            assignment.agreement_started_at = assignment.agreement_started_at or timestamp
        if target in {
            ProviderAssignmentStatus.terminated,
            ProviderAssignmentStatus.replaced,
        }:
            if not reason:
                raise ProviderAssignmentError("terminal_transition_requires_reason")
            assignment.termination_reason = reason
            assignment.active_until = timestamp
            assignment.agreement_ended_at = timestamp
        assignment.status = target.value
        self.session.flush()
        return assignment

    def replace_assignment(
        self,
        current_assignment_id: int,
        replacement_assignment_id: int,
        *,
        reason: str,
        at: datetime | None = None,
    ) -> tuple[ProviderAssignmentORM, ProviderAssignmentORM]:
        current = self.session.get(ProviderAssignmentORM, current_assignment_id)
        replacement = self.session.get(ProviderAssignmentORM, replacement_assignment_id)
        if current is None or replacement is None:
            raise ProviderAssignmentError("assignment_not_found")
        if current.property_id != replacement.property_id:
            raise ProviderAssignmentError("replacement_property_mismatch")
        if current.status != ProviderAssignmentStatus.active.value:
            raise ProviderAssignmentError("replacement_requires_active_assignment")
        timestamp = at or datetime.now(UTC)
        self.transition(
            current.id,
            ProviderAssignmentStatus.replaced,
            reason=reason,
            at=timestamp,
        )
        replacement.replaced_assignment_id = current.id
        if replacement.status == ProviderAssignmentStatus.candidate.value:
            self.transition(replacement.id, ProviderAssignmentStatus.pilot, at=timestamp)
        self.transition(replacement.id, ProviderAssignmentStatus.active, at=timestamp)
        self.session.flush()
        return current, replacement

    async def configure_call_routing(
        self,
        property_id: str,
        adapter: CallTrackingAdapter,
    ) -> str:
        profile = self._profile(property_id)
        assignment = self._active_assignment(property_id)
        if profile is None or assignment is None:
            raise ProviderAssignmentError("active_route_not_found")
        if profile.public_tracking_number is None or assignment.destination_phone is None:
            raise ProviderAssignmentError("phone_route_not_configured")
        result = await adapter.configure_route(
            CallRouteRequest(
                property_id=property_id,
                public_number=profile.public_tracking_number,
                destination_number=assignment.destination_phone,
                recording_enabled=profile.recording_approved,
                recording_retention_days=profile.recording_retention_days,
            )
        )
        profile.call_adapter_name = adapter.name
        profile.call_provider_route_id = result.provider_route_id
        profile.routing_health_status = result.status
        profile.routing_health_checked_at = datetime.now(UTC)
        self.session.flush()
        return result.provider_route_id

    async def check_call_routing_health(
        self,
        property_id: str,
        adapter: CallTrackingAdapter,
    ) -> bool:
        profile = self._profile(property_id)
        if profile is None:
            raise ProviderAssignmentError("routing_profile_not_found")
        health = await adapter.health_check(property_id)
        profile.routing_health_status = health.status
        profile.routing_health_checked_at = health.checked_at
        self.session.flush()
        return health.healthy

    def _profile(self, property_id: str) -> PropertyRoutingProfileORM | None:
        return self.session.scalar(
            select(PropertyRoutingProfileORM).where(
                PropertyRoutingProfileORM.property_id == property_id
            )
        )

    def _active_assignment(self, property_id: str) -> ProviderAssignmentORM | None:
        return self.session.scalar(
            select(ProviderAssignmentORM).where(
                ProviderAssignmentORM.property_id == property_id,
                ProviderAssignmentORM.status == ProviderAssignmentStatus.active.value,
            )
        )


class AnalyticsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(self, event: AnalyticsEventInput) -> AnalyticsEventORM:
        existing = self.session.scalar(
            select(AnalyticsEventORM).where(
                AnalyticsEventORM.source_name == event.source_name,
                AnalyticsEventORM.source_event_id == event.source_event_id,
            )
        )
        if existing is not None:
            return existing
        if event.lead_id is not None:
            lead = self.session.get(LeadORM, event.lead_id)
            if lead is None or lead.property_id != event.property_id:
                raise LeadRoutingError("analytics_lead_property_mismatch")
        row = AnalyticsEventORM(
            property_id=event.property_id,
            lead_id=event.lead_id,
            event_type=event.event_type.value,
            truth_basis=event.truth_basis.value,
            source_type=event.source_type.value,
            source_name=event.source_name,
            source_event_id=event.source_event_id,
            occurred_at=event.occurred_at,
            value_usd=event.value_usd,
            payload=redact_pii(event.payload),
        )
        self.session.add(row)
        if event.lead_id is not None and event.event_type.value in {
            "qualified_lead",
            "provider_acceptance",
            "appointment",
            "won_job",
            "lost_job",
            "revenue",
        }:
            self.session.add(
                LeadOutcomeORM(
                    lead_id=event.lead_id,
                    outcome_type=event.event_type.value,
                    truth_basis=event.truth_basis.value,
                    source_type=event.source_type.value,
                    source_name=event.source_name,
                    source_event_id=event.source_event_id,
                    occurred_at=event.occurred_at,
                    value_usd=event.value_usd,
                )
            )
        self.session.flush()
        return row


class LeadRoutingService:
    def __init__(
        self,
        session: Session,
        *,
        policy: LeadRoutingPolicy,
        adapters: dict[DeliveryChannel, DeliveryAdapter],
        spam_assessor: SpamAssessor,
        rate_limiter: RateLimiter,
        alert_adapter: OperatorAlertAdapter,
    ) -> None:
        self.session = session
        self.policy = policy
        self.adapters = adapters
        self.spam_assessor = spam_assessor
        self.rate_limiter = rate_limiter
        self.alert_adapter = alert_adapter

    async def submit(
        self,
        form: LeadForm,
        context: RequestContext,
    ) -> LeadSubmissionResult:
        replay = self.session.scalar(
            select(LeadORM).where(
                LeadORM.property_id == form.property_id,
                LeadORM.idempotency_key == form.idempotency_key,
            )
        )
        if replay is not None:
            return LeadSubmissionResult(
                lead_id=replay.id,
                status=LeadStatus(replay.status),
                idempotent_replay=True,
                delivery_ids=self._delivery_ids(replay.id),
            )

        profile = self.session.scalar(
            select(PropertyRoutingProfileORM).where(
                PropertyRoutingProfileORM.property_id == form.property_id,
                PropertyRoutingProfileORM.active.is_(True),
            )
        )
        if profile is None:
            raise LeadRoutingError("routing_profile_not_found")
        if form.consent_text_version != self.policy.consent_text_version:
            raise LeadRoutingError("stale_consent_text_version")
        if form.referral_disclosure_version != self.policy.referral_disclosure_version:
            raise LeadRoutingError("stale_referral_disclosure_version")

        fingerprint = stable_private_hash(
            f"{form.property_id}|{context.remote_address or 'unknown'}",
            self.policy.pii_hash_pepper,
        )
        if not self.rate_limiter.allow(
            fingerprint,
            limit=self.policy.rate_limit_count,
            window_seconds=self.policy.rate_limit_window_seconds,
        ):
            raise RateLimitExceeded("lead_rate_limit_exceeded")

        subject_hash = subject_fingerprint(
            form.email,
            form.phone,
            self.policy.pii_hash_pepper,
        )
        dedupe_hash = stable_private_hash(
            f"{form.property_id}|{subject_hash}",
            self.policy.pii_hash_pepper,
        )
        cutoff = context.received_at - timedelta(minutes=self.policy.dedupe_window_minutes)
        duplicate = self.session.scalar(
            select(LeadORM).where(
                LeadORM.property_id == form.property_id,
                LeadORM.dedupe_hash == dedupe_hash,
                LeadORM.received_at >= cutoff,
                LeadORM.pii_deleted_at.is_(None),
            )
        )
        if duplicate is not None:
            return LeadSubmissionResult(
                lead_id=duplicate.id,
                status=LeadStatus(duplicate.status),
                duplicate=True,
                delivery_ids=self._delivery_ids(duplicate.id),
            )

        assessment = self.spam_assessor.assess(form, context)
        assignment = self.session.scalar(
            select(ProviderAssignmentORM).where(
                ProviderAssignmentORM.property_id == form.property_id,
                ProviderAssignmentORM.status == ProviderAssignmentStatus.active.value,
            )
        )
        routable_channels = self._routable_channels(assignment)
        if assessment.disposition == "block":
            lead_status = LeadStatus.spam
        elif assignment is None or not routable_channels:
            lead_status = LeadStatus.delivery_failed
        else:
            lead_status = LeadStatus.routing
        lead_id = str(uuid.uuid4())
        retention_expires_at = context.received_at + timedelta(days=self.policy.retention_days)
        lead = LeadORM(
            id=lead_id,
            property_id=form.property_id,
            opportunity_id=profile.opportunity_id,
            provider_assignment_id=assignment.id if assignment is not None else None,
            channel=LeadChannel.form.value,
            status=lead_status.value,
            name=form.name,
            email=form.email,
            phone=form.phone,
            postal_code=form.postal_code,
            message=form.message,
            idempotency_key=form.idempotency_key,
            dedupe_hash=dedupe_hash,
            subject_hash=subject_hash,
            received_at=context.received_at,
            retention_expires_at=retention_expires_at,
        )
        self.session.add(lead)
        try:
            self.session.flush([lead])
        except IntegrityError as error:
            return self._idempotency_winner(form, error)
        self.session.add(
            ConsentRecordORM(
                lead_id=lead_id,
                consent_granted=form.consent_accepted,
                consent_text=self.policy.consent_text,
                consent_text_version=self.policy.consent_text_version,
                referral_disclosure_acknowledged=form.referral_disclosure_acknowledged,
                referral_disclosure_text=self.policy.referral_disclosure_text,
                referral_disclosure_version=self.policy.referral_disclosure_version,
                captured_at=context.received_at,
                request_fingerprint=fingerprint,
                proof_metadata={"request_id": context.request_id},
            )
        )
        self.session.add(
            SpamAssessmentORM(
                lead_id=lead_id,
                score=assessment.score,
                disposition=assessment.disposition,
                signals=assessment.signals,
                assessor_version=assessment.assessor_version,
            )
        )
        self._lead_event(
            lead_id=lead_id,
            event_type="form_submit",
            event_key=f"form:{form.idempotency_key}",
            occurred_at=context.received_at,
        )
        if lead_status == LeadStatus.routing:
            assert assignment is not None
            for channel, destination, adapter in routable_channels:
                self.session.add(
                    ProviderDeliveryORM(
                        id=str(uuid.uuid4()),
                        lead_id=lead_id,
                        provider_assignment_id=assignment.id,
                        delivery_key=f"{lead_id}:{assignment.id}:{channel.value}",
                        channel=channel.value,
                        destination_reference=masked_destination(destination),
                        adapter_name=adapter.name,
                        status=DeliveryStatus.pending.value,
                        attempt_count=0,
                        max_attempts=self.policy.maximum_delivery_attempts,
                        next_attempt_at=context.received_at,
                    )
                )
        try:
            self.session.commit()
        except IntegrityError as error:
            return self._idempotency_winner(form, error)

        if assessment.disposition == "block":
            logger.info(
                "Lead %s for property %s blocked by spam policy.",
                lead_id,
                form.property_id,
            )
            return LeadSubmissionResult(lead_id=lead_id, status=LeadStatus.spam)
        if lead_status == LeadStatus.delivery_failed:
            await self._alert_failure(
                property_id=form.property_id,
                lead_id=lead_id,
                reason_code=(
                    "no_active_provider"
                    if assignment is None
                    else "no_configured_delivery_channel"
                ),
            )
            return LeadSubmissionResult(
                lead_id=lead_id,
                status=LeadStatus.delivery_failed,
            )
        return LeadSubmissionResult(
            lead_id=lead_id,
            status=LeadStatus.routing,
            delivery_ids=self._delivery_ids(lead_id),
        )

    def _routable_channels(
        self,
        assignment: ProviderAssignmentORM | None,
    ) -> list[tuple[DeliveryChannel, str, DeliveryAdapter]]:
        if assignment is None:
            return []
        channels: list[tuple[DeliveryChannel, str, DeliveryAdapter]] = []
        destinations = (
            (DeliveryChannel.email, assignment.destination_email),
            (DeliveryChannel.phone, assignment.destination_phone),
        )
        for channel, destination in destinations:
            adapter = self.adapters.get(channel)
            if destination and adapter is not None:
                channels.append((channel, destination, adapter))
        return channels

    def _idempotency_winner(
        self,
        form: LeadForm,
        error: IntegrityError,
    ) -> LeadSubmissionResult:
        self.session.rollback()
        winner = self.session.scalar(
            select(LeadORM).where(
                LeadORM.property_id == form.property_id,
                LeadORM.idempotency_key == form.idempotency_key,
            )
        )
        if winner is None:
            raise error
        return LeadSubmissionResult(
            lead_id=winner.id,
            status=LeadStatus(winner.status),
            idempotent_replay=True,
            delivery_ids=self._delivery_ids(winner.id),
        )

    def _lead_event(
        self,
        *,
        lead_id: str,
        event_type: str,
        event_key: str,
        occurred_at: datetime,
    ) -> None:
        lead_is_new = any(
            isinstance(row, LeadORM) and row.id == lead_id for row in self.session.new
        )
        if not lead_is_new:
            existing = self.session.scalar(
                select(LeadEventORM.id).where(
                    LeadEventORM.lead_id == lead_id,
                    LeadEventORM.event_key == event_key,
                )
            )
            if existing is not None:
                return
        self.session.add(
            LeadEventORM(
                lead_id=lead_id,
                event_type=event_type,
                event_key=event_key,
                truth_basis="observed",
                source_type="system",
                source_name="lead-routing",
                occurred_at=occurred_at,
                payload={},
            )
        )

    def _delivery_ids(self, lead_id: str) -> list[str]:
        return list(
            self.session.scalars(
                select(ProviderDeliveryORM.id).where(ProviderDeliveryORM.lead_id == lead_id)
            )
        )

    async def _alert_failure(
        self,
        *,
        property_id: str,
        lead_id: str,
        reason_code: str,
    ) -> None:
        try:
            await self.alert_adapter.routing_failure(
                property_id=property_id,
                lead_id=lead_id,
                reason_code=reason_code,
            )
        except Exception:
            logger.error(
                "Operator alert failed for lead %s with reason %s.",
                lead_id,
                reason_code,
            )


def request_log_context(form: LeadForm, context: RequestContext) -> dict[str, object]:
    """Return an intentionally PII-free context for structured application logs."""
    return cast(
        dict[str, object],
        redact_pii(
            {
                "property_id": form.property_id,
                "idempotency_key": form.idempotency_key,
                "request_id": context.request_id,
                "remote_address": context.remote_address,
                "email": form.email,
                "phone": form.phone,
            }
        ),
    )
