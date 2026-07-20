from __future__ import annotations

from typing import Protocol

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


class DeliveryAdapter(Protocol):
    name: str
    channel: str

    async def deliver(self, request: DeliveryRequest) -> DeliveryResult: ...


class CallTrackingAdapter(Protocol):
    name: str

    async def configure_route(self, request: CallRouteRequest) -> CallRouteResult: ...

    async def health_check(self, property_id: str) -> RoutingHealth: ...


class SpamAssessor(Protocol):
    version: str

    def assess(self, form: LeadForm, context: RequestContext) -> SpamAssessmentResult: ...


class RateLimiter(Protocol):
    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool: ...


class OperatorAlertAdapter(Protocol):
    async def routing_failure(
        self,
        *,
        property_id: str,
        lead_id: str,
        reason_code: str,
    ) -> None: ...
