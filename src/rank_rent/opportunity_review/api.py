from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from rank_rent.db.base import get_session
from rank_rent.opportunity_review.models import (
    BatchConfirmationRequest,
    BatchPlanRequest,
    BatchQueueResult,
    DiscoveryTemplateInput,
    EvidenceOverrideRequest,
    EvidenceOverrideReversalRequest,
    EvidencePacketFormat,
    OwnershipRequest,
    ReviewActor,
    ReviewRole,
    ReviewTransitionRequest,
)
from rank_rent.opportunity_review.services import (
    OpportunityReviewError,
    OpportunityReviewService,
)
from rank_rent.security.audit import append_audit_event
from rank_rent.security.auth import Role, principal_from_request
from rank_rent.settings import get_settings

router = APIRouter(prefix="/api", tags=["opportunity-review"])


def review_actor(
    request: Request,
) -> ReviewActor:
    principal = principal_from_request(request)
    settings = get_settings()
    role_mapping = {
        Role.admin: ReviewRole.admin,
        Role.operator: ReviewRole.operator,
        Role.reviewer: ReviewRole.reviewer,
        # The review model predates read-only principals. Middleware prevents
        # mutations, while reviewer semantics permit evidence packet reads.
        Role.read_only: ReviewRole.reviewer,
    }
    actor_id = principal.user_id
    role = role_mapping[principal.role]
    if settings.app_env in {"local", "test", "development"}:
        actor_id = request.headers.get("x-actor-id", actor_id)
        local_role = request.headers.get("x-actor-role")
        if local_role:
            try:
                role = ReviewRole(local_role)
            except ValueError as exc:
                raise HTTPException(status_code=403, detail="Unknown review actor role.") from exc
            if role == ReviewRole.system:
                raise HTTPException(
                    status_code=403,
                    detail="System actors cannot be supplied by clients.",
                )
    return ReviewActor(actor_id=actor_id, role=role)


def _execute(
    session: Session,
    operation: Callable[[], Any],
    *,
    request: Request,
    event_type: str,
    target_type: str,
    target_id: str | int | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    try:
        result = operation()
        resolved_target_id = target_id
        if resolved_target_id is None:
            resolved_target_id = cast(str | int | None, getattr(result, "id", None))
        append_audit_event(
            session,
            event_type=event_type,
            actor=principal_from_request(request),
            target_type=target_type,
            target_id=str(resolved_target_id) if resolved_target_id is not None else None,
            request_id=getattr(request.state, "request_id", None),
            metadata=metadata,
        )
        session.commit()
        return result
    except OpportunityReviewError as exc:
        session.rollback()
        status = 404 if exc.code.endswith("_not_found") else 409
        if exc.code.endswith("_access_denied"):
            status = 403
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code, "context": exc.detail},
        ) from exc


@router.get("/opportunities/{opportunity_id}/review")
def get_opportunity_review(
    opportunity_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return OpportunityReviewService(session).review_summary(opportunity_id)
    except OpportunityReviewError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": exc.code, "context": exc.detail},
        ) from exc


@router.post("/opportunities/{opportunity_id}/review/transition")
def transition_opportunity(
    opportunity_id: int,
    payload: ReviewTransitionRequest,
    request: Request,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    _execute(
        session,
        lambda: service.transition(opportunity_id, payload, actor),
        request=request,
        event_type="opportunity.review.transition",
        target_type="opportunity",
        target_id=opportunity_id,
        metadata={"target_state": payload.target_state.value, "decision": payload.decision},
    )
    return service.review_summary(opportunity_id)


@router.post("/opportunities/{opportunity_id}/review/owner")
def assign_opportunity_owner(
    opportunity_id: int,
    payload: OwnershipRequest,
    request: Request,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    _execute(
        session,
        lambda: service.assign_owner(opportunity_id, payload, actor),
        request=request,
        event_type="opportunity.review.owner",
        target_type="opportunity",
        target_id=opportunity_id,
        metadata={"owner_user_id": payload.owner_user_id},
    )
    return service.review_summary(opportunity_id)


@router.get("/opportunities/{opportunity_id}/approval-completeness")
def get_approval_completeness(
    opportunity_id: int,
    maximum_age_days: int = Query(default=90, ge=1, le=730),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return OpportunityReviewService(session).approval_completeness(
            opportunity_id,
            maximum_age_days=maximum_age_days,
        ).model_dump(mode="json")
    except OpportunityReviewError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": exc.code, "context": exc.detail},
        ) from exc


@router.post("/opportunities/{opportunity_id}/overrides")
def apply_evidence_override(
    opportunity_id: int,
    payload: EvidenceOverrideRequest,
    request: Request,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    row = _execute(
        session,
        lambda: service.apply_override(opportunity_id, payload, actor),
        request=request,
        event_type="opportunity.evidence_override.apply",
        target_type="opportunity",
        target_id=opportunity_id,
        metadata={
            "override_kind": payload.override_kind.value,
            "target_record_id": payload.target_record_id,
            "field_name": payload.field_name,
        },
    )
    return {
        "override_id": row.id,
        "overrides": service.list_overrides(opportunity_id),
    }


@router.post("/opportunities/{opportunity_id}/overrides/{override_id}/revert")
def revert_evidence_override(
    opportunity_id: int,
    override_id: int,
    payload: EvidenceOverrideReversalRequest,
    request: Request,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    row = _execute(
        session,
        lambda: service.reverse_override(
            opportunity_id,
            override_id,
            payload,
            actor,
        ),
        request=request,
        event_type="opportunity.evidence_override.revert",
        target_type="evidence_override",
        target_id=override_id,
        metadata={"opportunity_id": opportunity_id},
    )
    return {
        "reversal_id": row.id,
        "overrides": service.list_overrides(opportunity_id),
    }


@router.get("/opportunities/{opportunity_id}/overrides")
def list_evidence_overrides(
    opportunity_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    try:
        service.review_summary(opportunity_id)
        return {"overrides": service.list_overrides(opportunity_id)}
    except OpportunityReviewError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": exc.code, "context": exc.detail},
        ) from exc


@router.get("/opportunities/{opportunity_id}/evidence-packet")
def export_evidence_packet(
    opportunity_id: int,
    format: EvidencePacketFormat = EvidencePacketFormat.json,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> Any:
    service = OpportunityReviewService(session)
    try:
        packet = service.evidence_packet(opportunity_id, actor)
    except OpportunityReviewError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": exc.code, "context": exc.detail},
        ) from exc
    if format == EvidencePacketFormat.csv:
        return Response(
            content=service.evidence_packet_csv(packet),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="opportunity-{opportunity_id}-evidence.csv"'
                )
            },
        )
    return packet.model_dump(mode="json")


@router.get("/discovery-templates")
def list_discovery_templates(
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {"templates": OpportunityReviewService(session).list_templates(actor)}


@router.post("/discovery-templates")
def create_discovery_template(
    payload: DiscoveryTemplateInput,
    request: Request,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    row = _execute(
        session,
        lambda: service.create_template(payload, actor),
        request=request,
        event_type="discovery_template.create",
        target_type="discovery_template",
        metadata={"name": payload.name},
    )
    return next(
        item for item in service.list_templates(actor) if item["id"] == row.id
    )


@router.put("/discovery-templates/{template_id}")
def update_discovery_template(
    template_id: int,
    payload: DiscoveryTemplateInput,
    request: Request,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    row = _execute(
        session,
        lambda: service.update_template(template_id, payload, actor),
        request=request,
        event_type="discovery_template.update",
        target_type="discovery_template",
        target_id=template_id,
        metadata={"name": payload.name},
    )
    return next(
        item for item in service.list_templates(actor) if item["id"] == row.id
    )


@router.delete("/discovery-templates/{template_id}")
def archive_discovery_template(
    template_id: int,
    request: Request,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    row = _execute(
        session,
        lambda: service.archive_template(template_id, actor),
        request=request,
        event_type="discovery_template.archive",
        target_type="discovery_template",
        target_id=template_id,
    )
    return {"template_id": row.id, "archived": True}


@router.post("/batch-scan-plans")
def create_batch_scan_plan(
    payload: BatchPlanRequest,
    request: Request,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    row = _execute(
        session,
        lambda: service.create_batch_plan(payload, actor),
        request=request,
        event_type="batch_scan_plan.create",
        target_type="batch_scan_plan",
        metadata={"name": payload.name},
    )
    return service.batch_payload(row.id)


@router.get("/batch-scan-plans/{batch_id}")
def get_batch_scan_plan(
    batch_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return OpportunityReviewService(session).batch_payload(batch_id)
    except OpportunityReviewError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": exc.code, "context": exc.detail},
        ) from exc


@router.post("/batch-scan-plans/{batch_id}/confirm")
def confirm_batch_scan_plan(
    batch_id: int,
    payload: BatchConfirmationRequest,
    request: Request,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    _execute(
        session,
        lambda: service.confirm_batch(batch_id, payload, actor),
        request=request,
        event_type="batch_scan_plan.confirm",
        target_type="batch_scan_plan",
        target_id=batch_id,
    )
    return service.batch_payload(batch_id)


@router.post("/batch-scan-plans/{batch_id}/queue")
def queue_batch_scan_plan(
    batch_id: int,
    request: Request,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    result = cast(
        BatchQueueResult,
        _execute(
            session,
            lambda: service.queue_batch(batch_id, actor),
            request=request,
            event_type="batch_scan_plan.queue",
            target_type="batch_scan_plan",
            target_id=batch_id,
        ),
    )
    return result.model_dump(mode="json")
