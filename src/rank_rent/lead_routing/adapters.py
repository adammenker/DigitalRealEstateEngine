from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from rank_rent.lead_routing.models import (
    CallRouteRequest,
    CallRouteResult,
    DeliveryRequest,
    DeliveryResult,
    LeadForm,
    RequestContext,
    RoutingHealth,
    SpamAssessmentResult,
)


class RetryableDeliveryError(RuntimeError):
    """A fixture-visible transient failure that is safe to retry."""


class FixtureDeliveryAdapter:
    """In-memory adapter. It never sends an email, text, or call."""

    def __init__(self, channel: str, *, fail_first_attempts: int = 0) -> None:
        self.name = f"fixture-{channel}"
        self.channel = channel
        self.fail_first_attempts = fail_first_attempts
        self.attempts: dict[str, int] = defaultdict(int)
        self.deliveries: dict[str, DeliveryResult] = {}

    async def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        existing = self.deliveries.get(request.delivery_key)
        if existing is not None:
            return existing
        self.attempts[request.delivery_key] += 1
        if self.attempts[request.delivery_key] <= self.fail_first_attempts:
            raise RetryableDeliveryError("fixture_transient_failure")
        provider_message_id = hashlib.sha256(
            f"{self.name}:{request.delivery_key}".encode()
        ).hexdigest()[:24]
        result = DeliveryResult(
            provider_message_id=provider_message_id,
            accepted=True,
            status="accepted",
            metadata={"fixture": True},
        )
        self.deliveries[request.delivery_key] = result
        return result


class FixtureCallTrackingAdapter:
    """In-memory route registry. It cannot provision or forward a real number."""

    name = "fixture-call-tracking"

    def __init__(self) -> None:
        self.routes: dict[str, CallRouteRequest] = {}

    async def configure_route(self, request: CallRouteRequest) -> CallRouteResult:
        self.routes[request.property_id] = request
        route_id = hashlib.sha256(request.property_id.encode()).hexdigest()[:24]
        return CallRouteResult(
            provider_route_id=route_id,
            public_number=request.public_number,
            status="configured_fixture",
        )

    async def health_check(self, property_id: str) -> RoutingHealth:
        return RoutingHealth(
            healthy=property_id in self.routes,
            status="healthy_fixture" if property_id in self.routes else "not_configured",
        )


class FixtureOperatorAlertAdapter:
    def __init__(self) -> None:
        self.alerts: list[dict[str, str]] = []

    async def routing_failure(
        self,
        *,
        property_id: str,
        lead_id: str,
        reason_code: str,
    ) -> None:
        self.alerts.append(
            {
                "property_id": property_id,
                "lead_id": lead_id,
                "reason_code": reason_code,
            }
        )


class LocalSpamAssessor:
    version = "local-v1"

    def assess(self, form: LeadForm, context: RequestContext) -> SpamAssessmentResult:
        signals: list[str] = []
        score = 0.0
        if form.honeypot:
            signals.append("honeypot_populated")
            score += 1.0
        if form.message and form.message.lower().count("http") >= 3:
            signals.append("excessive_links")
            score += 0.5
        if not context.remote_address:
            signals.append("missing_remote_address")
            score += 0.1
        disposition = "block" if score >= 0.9 else "review" if score >= 0.5 else "allow"
        return SpamAssessmentResult(
            score=min(score, 1.0),
            disposition=disposition,
            signals=signals,
            assessor_version=self.version,
        )


class InMemoryRateLimiter:
    """Process-local hook suitable for tests; production must supply a shared backend."""

    def __init__(self) -> None:
        self._events: dict[str, deque[datetime]] = defaultdict(deque)

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=window_seconds)
        events = self._events[key]
        while events and events[0] < cutoff:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True
