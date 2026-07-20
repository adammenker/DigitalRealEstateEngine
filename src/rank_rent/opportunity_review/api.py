from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
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
from rank_rent.settings import get_settings

router = APIRouter(prefix="/api", tags=["opportunity-review"])


def review_actor(
    x_actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    x_actor_role: Annotated[str | None, Header(alias="X-Actor-Role")] = None,
) -> ReviewActor:
    settings = get_settings()
    if settings.app_env.strip().lower() in {"production", "staging"} and not x_actor_id:
        raise HTTPException(
            status_code=401,
            detail="An authenticated actor is required outside local/test environments.",
        )
    actor_id = x_actor_id or "local-operator"
    role_value = x_actor_role or "operator"
    try:
        role = ReviewRole(role_value)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Unknown review actor role.") from exc
    if role == ReviewRole.system:
        raise HTTPException(status_code=403, detail="System actors cannot be supplied by clients.")
    return ReviewActor(actor_id=actor_id, role=role)


def _execute(
    session: Session,
    operation: Any,
) -> Any:
    try:
        result = operation()
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
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    _execute(session, lambda: service.transition(opportunity_id, payload, actor))
    return service.review_summary(opportunity_id)


@router.post("/opportunities/{opportunity_id}/review/owner")
def assign_opportunity_owner(
    opportunity_id: int,
    payload: OwnershipRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    _execute(session, lambda: service.assign_owner(opportunity_id, payload, actor))
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
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    row = _execute(
        session,
        lambda: service.apply_override(opportunity_id, payload, actor),
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
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    row = _execute(session, lambda: service.create_template(payload, actor))
    return next(
        item for item in service.list_templates(actor) if item["id"] == row.id
    )


@router.put("/discovery-templates/{template_id}")
def update_discovery_template(
    template_id: int,
    payload: DiscoveryTemplateInput,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    row = _execute(
        session,
        lambda: service.update_template(template_id, payload, actor),
    )
    return next(
        item for item in service.list_templates(actor) if item["id"] == row.id
    )


@router.delete("/discovery-templates/{template_id}")
def archive_discovery_template(
    template_id: int,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    row = _execute(
        session,
        lambda: service.archive_template(template_id, actor),
    )
    return {"template_id": row.id, "archived": True}


@router.post("/batch-scan-plans")
def create_batch_scan_plan(
    payload: BatchPlanRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    row = _execute(session, lambda: service.create_batch_plan(payload, actor))
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
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    _execute(session, lambda: service.confirm_batch(batch_id, payload, actor))
    return service.batch_payload(batch_id)


@router.post("/batch-scan-plans/{batch_id}/queue")
def queue_batch_scan_plan(
    batch_id: int,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = OpportunityReviewService(session)
    result = cast(
        BatchQueueResult,
        _execute(session, lambda: service.queue_batch(batch_id, actor)),
    )
    return result.model_dump(mode="json")
