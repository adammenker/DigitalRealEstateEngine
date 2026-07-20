from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import MarketORM, OpportunityORM, ServiceFamilyORM
from rank_rent.lead_routing.adapters import (
    FixtureCallTrackingAdapter,
    FixtureDeliveryAdapter,
    FixtureOperatorAlertAdapter,
    InMemoryRateLimiter,
    LocalSpamAssessor,
)
from rank_rent.lead_routing.models import (
    AccessContext,
    AnalyticsEventInput,
    AnalyticsEventType,
    AnalyticsSourceType,
    DeliveryChannel,
    LeadAccessRole,
    LeadForm,
    LeadRoutingPolicy,
    ProviderAssignmentStatus,
    RequestContext,
    TruthBasis,
)
from rank_rent.lead_routing.orm import (
    AnalyticsEventORM,
    ConsentRecordORM,
    LeadORM,
    LeadOutcomeORM,
    PropertyRoutingProfileORM,
    ProviderAssignmentORM,
    ProviderDeliveryORM,
    RoutingAttemptORM,
)
from rank_rent.lead_routing.privacy import LeadAccessDenied, LeadPrivacyService
from rank_rent.lead_routing.services import (
    AnalyticsService,
    LeadRoutingError,
    LeadRoutingService,
    ProviderOperationsService,
    RateLimitExceeded,
    request_log_context,
)


@pytest.fixture
def session() -> Session:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as active_session:
        service = ServiceFamilyORM(slug="plumbing", display_name="Plumbing")
        market = MarketORM(slug="st-louis-mo", display_name="St. Louis, MO")
        active_session.add_all([service, market])
        active_session.flush()
        active_session.add(
            OpportunityORM(
                id=1,
                service_family_id=service.id,
                market_id=market.id,
                status="approved",
            )
        )
        active_session.commit()
        yield active_session


def _form(*, key: str = "lead-request-0001", email: str = "Ada@Example.com") -> LeadForm:
    return LeadForm(
        property_id="property-1",
        name="Ada Lovelace",
        email=email,
        phone="314-555-0199",
        postal_code="63101",
        message="I need a plumber this week.",
        consent_accepted=True,
        consent_text_version="consent-v1",
        referral_disclosure_acknowledged=True,
        referral_disclosure_version="referral-v1",
        idempotency_key=key,
    )


def _context(*, request_id: str = "request-1") -> RequestContext:
    return RequestContext(
        remote_address="192.0.2.10",
        user_agent="test browser",
        request_id=request_id,
    )


def _policy(**overrides: object) -> LeadRoutingPolicy:
    values: dict[str, object] = {
        "pii_hash_pepper": "test-only-private-pepper",
        "maximum_delivery_attempts": 3,
    }
    values.update(overrides)
    return LeadRoutingPolicy.model_validate(values)


def _active_assignment(session: Session) -> tuple[ProviderOperationsService, ProviderAssignmentORM]:
    operations = ProviderOperationsService(session)
    operations.create_routing_profile(
        property_id="property-1",
        opportunity_id=1,
        public_tracking_number="+13145550100",
        public_contact_email="leads@property.test",
    )
    assignment = operations.create_assignment(
        property_id="property-1",
        public_business_name="Fixture Plumbing",
        destination_email="dispatch@provider.test",
        destination_phone="+13145550111",
        coverage={"postal_codes": ["63101"]},
        response_expectation_minutes=15,
    )
    operations.transition(assignment.id, ProviderAssignmentStatus.pilot)
    operations.transition(assignment.id, ProviderAssignmentStatus.active)
    session.commit()
    return operations, assignment


def test_form_validation_requires_contact_consent_and_disclosure() -> None:
    with pytest.raises(ValidationError):
        LeadForm(
            property_id="property-1",
            name="Ada",
            consent_accepted=False,
            consent_text_version="v1",
            referral_disclosure_acknowledged=False,
            referral_disclosure_version="v1",
            idempotency_key="valid-key-0001",
        )


@pytest.mark.asyncio
async def test_stale_consent_copy_is_rejected_before_persistence(
    session: Session,
) -> None:
    _active_assignment(session)
    router = LeadRoutingService(
        session,
        policy=_policy(),
        adapters={DeliveryChannel.email: FixtureDeliveryAdapter("email")},
        spam_assessor=LocalSpamAssessor(),
        rate_limiter=InMemoryRateLimiter(),
        alert_adapter=FixtureOperatorAlertAdapter(),
    )
    stale = _form().model_copy(update={"consent_text_version": "old-consent"})
    with pytest.raises(LeadRoutingError, match="stale_consent"):
        await router.submit(stale, _context())
    assert session.query(LeadORM).count() == 0


@pytest.mark.asyncio
async def test_lead_routing_retries_deduplicates_and_preserves_consent(
    session: Session,
) -> None:
    _, assignment = _active_assignment(session)
    email = FixtureDeliveryAdapter("email", fail_first_attempts=1)
    phone = FixtureDeliveryAdapter("phone")
    alerts = FixtureOperatorAlertAdapter()
    router = LeadRoutingService(
        session,
        policy=_policy(),
        adapters={
            DeliveryChannel.email: email,
            DeliveryChannel.phone: phone,
        },
        spam_assessor=LocalSpamAssessor(),
        rate_limiter=InMemoryRateLimiter(),
        alert_adapter=alerts,
    )

    result = await router.submit(_form(), _context())

    assert result.status.value == "delivered"
    assert len(result.delivery_ids) == 2
    lead = session.get(LeadORM, result.lead_id)
    assert lead is not None
    assert lead.provider_assignment_id == assignment.id
    assert lead.email == "ada@example.com"
    consent = session.scalar(select(ConsentRecordORM).where(ConsentRecordORM.lead_id == lead.id))
    assert consent is not None
    assert consent.consent_text_version == "consent-v1"
    assert consent.consent_text == ("I consent to being contacted about this service request.")
    assert "independent service provider" in consent.referral_disclosure_text
    attempts = list(
        session.scalars(select(RoutingAttemptORM).where(RoutingAttemptORM.lead_id == lead.id))
    )
    assert len(attempts) == 3
    assert {attempt.status for attempt in attempts} == {
        "retryable_failure",
        "succeeded",
    }
    deliveries = list(
        session.scalars(select(ProviderDeliveryORM).where(ProviderDeliveryORM.lead_id == lead.id))
    )
    assert all("dispatch@" not in row.destination_reference for row in deliveries)
    assert alerts.alerts == []

    lead.status = "routing"
    session.commit()
    replay = await router.submit(_form(), _context(request_id="request-2"))
    assert replay.idempotent_replay is True
    assert replay.lead_id == lead.id
    assert replay.status.value == "delivered"
    assert session.query(RoutingAttemptORM).count() == 3

    duplicate = await router.submit(
        _form(key="lead-request-0002"),
        _context(request_id="request-3"),
    )
    assert duplicate.duplicate is True
    assert duplicate.lead_id == lead.id
    assert session.query(LeadORM).count() == 1


@pytest.mark.asyncio
async def test_spam_and_rate_limit_hooks_stop_delivery(session: Session) -> None:
    _active_assignment(session)
    adapter = FixtureDeliveryAdapter("email")
    router = LeadRoutingService(
        session,
        policy=_policy(rate_limit_count=1),
        adapters={DeliveryChannel.email: adapter},
        spam_assessor=LocalSpamAssessor(),
        rate_limiter=InMemoryRateLimiter(),
        alert_adapter=FixtureOperatorAlertAdapter(),
    )
    spam_form = _form().model_copy(update={"honeypot": "filled"})
    spam = await router.submit(spam_form, _context())
    assert spam.status.value == "spam"
    assert adapter.deliveries == {}

    with pytest.raises(RateLimitExceeded):
        await router.submit(
            _form(key="lead-request-0003", email="other@example.com"),
            _context(request_id="request-3"),
        )


@pytest.mark.asyncio
async def test_provider_replacement_preserves_public_number_and_reconfigures_route(
    session: Session,
) -> None:
    operations, current = _active_assignment(session)
    replacement = operations.create_assignment(
        property_id="property-1",
        public_business_name="Replacement Plumbing",
        destination_phone="+13145550222",
    )
    old, active = operations.replace_assignment(
        current.id,
        replacement.id,
        reason="Pilot agreement ended.",
    )
    session.commit()

    profile = session.scalar(
        select(PropertyRoutingProfileORM).where(
            PropertyRoutingProfileORM.property_id == "property-1"
        )
    )
    assert profile is not None
    assert profile.public_tracking_number == "+13145550100"
    assert old.status == "replaced"
    assert active.status == "active"
    assert active.replaced_assignment_id == old.id
    call_adapter = FixtureCallTrackingAdapter()
    route_id = await operations.configure_call_routing("property-1", call_adapter)
    healthy = await operations.check_call_routing_health("property-1", call_adapter)
    assert route_id
    assert healthy is True
    assert profile.call_adapter_name == "fixture-call-tracking"
    assert profile.call_provider_route_id == route_id
    assert profile.routing_health_status == "healthy_fixture"
    assert call_adapter.routes["property-1"].destination_number == "+13145550222"
    assert call_adapter.routes["property-1"].recording_enabled is False


@pytest.mark.asyncio
async def test_failed_delivery_alerts_without_exposing_pii(session: Session) -> None:
    _active_assignment(session)
    alerts = FixtureOperatorAlertAdapter()
    router = LeadRoutingService(
        session,
        policy=_policy(maximum_delivery_attempts=2),
        adapters={
            DeliveryChannel.email: FixtureDeliveryAdapter(
                "email",
                fail_first_attempts=5,
            )
        },
        spam_assessor=LocalSpamAssessor(),
        rate_limiter=InMemoryRateLimiter(),
        alert_adapter=alerts,
    )
    result = await router.submit(_form(), _context())
    assert result.status.value == "delivery_failed"
    assert alerts.alerts == [
        {
            "property_id": "property-1",
            "lead_id": result.lead_id,
            "reason_code": "all_deliveries_failed",
        }
    ]
    log_context = request_log_context(_form(), _context())
    assert log_context["email"] == "<redacted>"
    assert log_context["phone"] == "<redacted>"
    assert log_context["remote_address"] == "<redacted>"


@pytest.mark.asyncio
async def test_analytics_preserves_truth_source_and_creates_lead_outcome(
    session: Session,
) -> None:
    _active_assignment(session)
    router = LeadRoutingService(
        session,
        policy=_policy(),
        adapters={DeliveryChannel.email: FixtureDeliveryAdapter("email")},
        spam_assessor=LocalSpamAssessor(),
        rate_limiter=InMemoryRateLimiter(),
        alert_adapter=FixtureOperatorAlertAdapter(),
    )
    lead_result = await router.submit(_form(), _context())
    service = AnalyticsService(session)
    event = service.record(
        AnalyticsEventInput(
            property_id="property-1",
            lead_id=lead_result.lead_id,
            event_type=AnalyticsEventType.won_job,
            source_type=AnalyticsSourceType.provider,
            source_name="fixture-provider-portal",
            source_event_id="provider-event-1",
            truth_basis=TruthBasis.provider_reported,
            payload={"name": "Ada", "job_type": "repair"},
        )
    )
    session.commit()

    assert event.payload["name"] == "<redacted>"
    assert event.payload["job_type"] == "repair"
    outcome = session.scalar(
        select(LeadOutcomeORM).where(LeadOutcomeORM.lead_id == lead_result.lead_id)
    )
    assert outcome is not None
    assert outcome.truth_basis == "provider_reported"
    assert outcome.source_type == "provider"
    assert session.query(AnalyticsEventORM).count() == 1

    replay = service.record(
        AnalyticsEventInput(
            property_id="property-1",
            lead_id=lead_result.lead_id,
            event_type=AnalyticsEventType.won_job,
            source_type=AnalyticsSourceType.provider,
            source_name="fixture-provider-portal",
            source_event_id="provider-event-1",
            truth_basis=TruthBasis.provider_reported,
        )
    )
    assert replay.id == event.id
    assert session.query(LeadOutcomeORM).count() == 1


@pytest.mark.asyncio
async def test_privacy_export_access_deletion_and_retention(session: Session) -> None:
    _, assignment = _active_assignment(session)
    router = LeadRoutingService(
        session,
        policy=_policy(retention_days=1),
        adapters={DeliveryChannel.email: FixtureDeliveryAdapter("email")},
        spam_assessor=LocalSpamAssessor(),
        rate_limiter=InMemoryRateLimiter(),
        alert_adapter=FixtureOperatorAlertAdapter(),
    )
    result = await router.submit(_form(), _context())
    privacy = LeadPrivacyService(session)
    provider_access = AccessContext(
        actor_id="provider-user",
        role=LeadAccessRole.provider,
        provider_assignment_ids={assignment.id},
    )
    exported = privacy.export_lead(result.lead_id, provider_access)
    assert exported["lead"]["email"] == "ada@example.com"

    with pytest.raises(LeadAccessDenied):
        privacy.export_lead(
            result.lead_id,
            AccessContext(
                actor_id="other-provider",
                role=LeadAccessRole.provider,
                provider_assignment_ids={999},
            ),
        )
    with pytest.raises(LeadAccessDenied):
        privacy.delete_lead(result.lead_id, provider_access)

    deleted_ids = privacy.enforce_retention(now=datetime.now(UTC) + timedelta(days=2))
    session.commit()
    assert deleted_ids == [result.lead_id]
    lead = session.get(LeadORM, result.lead_id)
    assert lead is not None
    assert lead.name == "[deleted]"
    assert lead.email is None
    assert lead.phone is None
    assert lead.status == "deleted"
    assert lead.subject_hash == f"deleted:{lead.id}"
    delivery = session.scalar(
        select(ProviderDeliveryORM).where(ProviderDeliveryORM.lead_id == lead.id)
    )
    assert delivery is not None
    assert delivery.destination_reference == "<deleted>"
    assert delivery.provider_message_id is None
