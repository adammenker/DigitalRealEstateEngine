from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.lead_routing.models import AccessContext, LeadAccessRole
from rank_rent.lead_routing.orm import (
    AnalyticsEventORM,
    ConsentRecordORM,
    LeadEventORM,
    LeadORM,
    LeadOutcomeORM,
    ProviderDeliveryORM,
)

_SENSITIVE_KEYS = {
    "address",
    "api_key",
    "authorization",
    "contact_email",
    "contact_name",
    "contact_phone",
    "customer_email",
    "destination",
    "email",
    "full_name",
    "ip",
    "ip_address",
    "message",
    "name",
    "phone",
    "postal_code",
    "password",
    "remote_address",
    "secret",
    "token",
    "user_agent",
}


class LeadAccessDenied(PermissionError):
    pass


def stable_private_hash(value: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def subject_fingerprint(email: str | None, phone: str | None, pepper: str) -> str:
    normalized = f"{(email or '').strip().lower()}|{(phone or '').strip()}"
    return stable_private_hash(normalized, pepper)


def redact_pii(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>" if str(key).lower() in _SENSITIVE_KEYS else redact_pii(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [redact_pii(item) for item in value]
    return value


def masked_destination(destination: str) -> str:
    if "@" in destination:
        local, domain = destination.split("@", 1)
        return f"{local[:1]}***@{domain}"
    digits = "".join(character for character in destination if character.isdigit())
    return f"***{digits[-4:]}" if digits else "<configured>"


class LeadPrivacyService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def export_lead(self, lead_id: str, access: AccessContext) -> dict[str, Any]:
        lead = self.session.get(LeadORM, lead_id)
        if lead is None:
            raise LookupError("lead_not_found")
        self._authorize_lead(access, lead)
        consent = self.session.scalar(
            select(ConsentRecordORM).where(ConsentRecordORM.lead_id == lead.id)
        )
        events = list(
            self.session.scalars(
                select(LeadEventORM)
                .where(LeadEventORM.lead_id == lead.id)
                .order_by(LeadEventORM.occurred_at)
            )
        )
        deliveries = list(
            self.session.scalars(
                select(ProviderDeliveryORM).where(ProviderDeliveryORM.lead_id == lead.id)
            )
        )
        return {
            "lead": {
                "id": lead.id,
                "property_id": lead.property_id,
                "status": lead.status,
                "name": lead.name,
                "email": lead.email,
                "phone": lead.phone,
                "postal_code": lead.postal_code,
                "message": lead.message,
                "received_at": lead.received_at.isoformat(),
                "pii_deleted_at": (
                    lead.pii_deleted_at.isoformat() if lead.pii_deleted_at else None
                ),
            },
            "consent": (
                {
                    "consent_granted": consent.consent_granted,
                    "consent_text": consent.consent_text,
                    "consent_text_version": consent.consent_text_version,
                    "referral_disclosure_acknowledged": (consent.referral_disclosure_acknowledged),
                    "referral_disclosure_text": consent.referral_disclosure_text,
                    "referral_disclosure_version": consent.referral_disclosure_version,
                    "captured_at": consent.captured_at.isoformat(),
                }
                if consent
                else None
            ),
            "events": [
                {
                    "event_type": event.event_type,
                    "truth_basis": event.truth_basis,
                    "source_type": event.source_type,
                    "occurred_at": event.occurred_at.isoformat(),
                    "payload": event.payload,
                }
                for event in events
            ],
            "deliveries": [
                {
                    "channel": delivery.channel,
                    "destination_reference": delivery.destination_reference,
                    "status": delivery.status,
                    "delivered_at": (
                        delivery.delivered_at.isoformat() if delivery.delivered_at else None
                    ),
                }
                for delivery in deliveries
            ],
        }

    def delete_lead(self, lead_id: str, access: AccessContext) -> bool:
        if access.role not in {LeadAccessRole.operator, LeadAccessRole.privacy_admin}:
            raise LeadAccessDenied("lead_deletion_requires_privileged_role")
        lead = self.session.get(LeadORM, lead_id)
        if lead is None:
            return False
        self._anonymize(lead)
        self.session.flush()
        return True

    def enforce_retention(self, *, now: datetime | None = None) -> list[str]:
        effective_now = now or datetime.now(UTC)
        rows = list(
            self.session.scalars(
                select(LeadORM).where(
                    LeadORM.retention_expires_at <= effective_now,
                    LeadORM.pii_deleted_at.is_(None),
                )
            )
        )
        for row in rows:
            self._anonymize(row, deleted_at=effective_now)
        self.session.flush()
        return [row.id for row in rows]

    def _authorize_lead(self, access: AccessContext, lead: LeadORM) -> None:
        if access.role in {LeadAccessRole.operator, LeadAccessRole.privacy_admin}:
            return
        if (
            access.role == LeadAccessRole.provider
            and lead.provider_assignment_id is not None
            and lead.provider_assignment_id in access.provider_assignment_ids
        ):
            return
        raise LeadAccessDenied("lead_access_denied")

    def _anonymize(
        self,
        lead: LeadORM,
        *,
        deleted_at: datetime | None = None,
    ) -> None:
        timestamp = deleted_at or datetime.now(UTC)
        lead.name = "[deleted]"
        lead.email = None
        lead.phone = None
        lead.postal_code = None
        lead.message = None
        lead.idempotency_key = f"deleted:{lead.id}"
        lead.dedupe_hash = f"deleted:{lead.id}"
        lead.subject_hash = f"deleted:{lead.id}"
        lead.status = "deleted"
        lead.pii_deleted_at = timestamp
        consent = self.session.scalar(
            select(ConsentRecordORM).where(ConsentRecordORM.lead_id == lead.id)
        )
        if consent is not None:
            consent.request_fingerprint = "deleted"
            consent.proof_metadata = {}
        deliveries = self.session.scalars(
            select(ProviderDeliveryORM).where(ProviderDeliveryORM.lead_id == lead.id)
        )
        for delivery in deliveries:
            delivery.destination_reference = "<deleted>"
            delivery.provider_message_id = None
        outcomes = self.session.scalars(
            select(LeadOutcomeORM).where(LeadOutcomeORM.lead_id == lead.id)
        )
        for outcome in outcomes:
            outcome.notes = None
            outcome.source_event_id = f"deleted:{outcome.id}"
        events = self.session.scalars(select(LeadEventORM).where(LeadEventORM.lead_id == lead.id))
        for event in events:
            event.event_key = f"deleted:{event.id}"
            event.payload = {}
        analytics_events = self.session.scalars(
            select(AnalyticsEventORM).where(AnalyticsEventORM.lead_id == lead.id)
        )
        for analytics_event in analytics_events:
            analytics_event.lead_id = None
            analytics_event.source_event_id = f"deleted:{analytics_event.id}"
            analytics_event.payload = {}
