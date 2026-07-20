from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from rank_rent.db.base import get_session
from rank_rent.opportunity_review.api import review_actor
from rank_rent.opportunity_review.models import ReviewActor
from rank_rent.property_workflow.adapters import FixtureDomainAvailabilityAdapter
from rank_rent.property_workflow.models import (
    AssetApprovalRequest,
    AssetCreateRequest,
    ComplianceReviewRequest,
    DeploymentRequest,
    DNSVerificationRequest,
    DomainAvailabilityRequest,
    DomainDecisionRequest,
    DomainGenerationRequest,
    DomainPurchaseApprovalRequest,
    ManualRegistrationRequest,
    PropertyCreateRequest,
    PropertyUpdateRequest,
    ProviderActivationRequest,
    ProviderAssignmentInput,
    ProviderReplacementRequest,
    RollbackRequest,
    SiteBuildRequest,
    SiteConfigApprovalRequest,
    SiteConfigInput,
)
from rank_rent.property_workflow.services import (
    PropertyWorkflowError,
    PropertyWorkflowService,
)

router = APIRouter(prefix="/api", tags=["property-workflow"])


def _failure(exc: PropertyWorkflowError) -> HTTPException:
    status = 404 if exc.code.endswith("_not_found") else 409
    if exc.code.endswith("_role_required"):
        status = 403
    return HTTPException(
        status_code=status,
        detail={"code": exc.code, "context": exc.detail},
    )


def _execute(session: Session, operation: Any) -> Any:
    try:
        result = operation()
        session.commit()
        return result
    except PropertyWorkflowError as exc:
        session.rollback()
        raise _failure(exc) from exc


async def _execute_async(session: Session, operation: Any) -> Any:
    try:
        result = await operation()
        session.commit()
        return result
    except PropertyWorkflowError as exc:
        session.rollback()
        raise _failure(exc) from exc


@router.get("/properties")
def list_properties(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {"properties": PropertyWorkflowService(session).list_properties()}


@router.post("/opportunities/{opportunity_id}/property")
def create_property(
    opportunity_id: int,
    payload: PropertyCreateRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = _execute(
        session,
        lambda: service.create_property(opportunity_id, payload, actor),
    )
    return service.summary(row.id)


@router.get("/properties/{property_id}")
def get_property(
    property_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return PropertyWorkflowService(session).summary(property_id)
    except PropertyWorkflowError as exc:
        raise _failure(exc) from exc


@router.patch("/properties/{property_id}")
def update_property(
    property_id: str,
    payload: PropertyUpdateRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    _execute(session, lambda: service.update_property(property_id, payload, actor))
    return service.summary(property_id)


@router.post("/properties/{property_id}/domain-candidates/generate")
def generate_domain_candidates(
    property_id: str,
    payload: DomainGenerationRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    _execute(
        session,
        lambda: service.generate_domain_candidates(property_id, payload),
    )
    return service.summary(property_id)


@router.post("/domain-candidates/{candidate_id}/shortlist")
def shortlist_domain(
    candidate_id: int,
    payload: DomainDecisionRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = _execute(
        session,
        lambda: service.decide_domain_candidate(
            candidate_id,
            shortlist=True,
            request=payload,
            actor=actor,
        ),
    )
    return service.summary(row.property_id)


@router.post("/domain-candidates/{candidate_id}/reject")
def reject_domain(
    candidate_id: int,
    payload: DomainDecisionRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = _execute(
        session,
        lambda: service.decide_domain_candidate(
            candidate_id,
            shortlist=False,
            request=payload,
            actor=actor,
        ),
    )
    return service.summary(row.property_id)


@router.post("/domain-candidates/{candidate_id}/availability")
async def check_domain_availability(
    candidate_id: int,
    payload: DomainAvailabilityRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = await _execute_async(
        session,
        lambda: service.check_domain_availability(
            candidate_id,
            FixtureDomainAvailabilityAdapter(
                payload.fixture_status,
                payload.evidence,
            ),
        ),
    )
    return service.summary(row.property_id)


@router.post("/domain-candidates/{candidate_id}/purchase-approval")
def approve_domain_purchase(
    candidate_id: int,
    payload: DomainPurchaseApprovalRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = _execute(
        session,
        lambda: service.approve_domain_purchase(candidate_id, payload, actor),
    )
    return service.summary(row.property_id)


@router.post("/domain-registrations/{registration_id}/manual-registration")
def record_manual_registration(
    registration_id: int,
    payload: ManualRegistrationRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = _execute(
        session,
        lambda: service.record_manual_registration(registration_id, payload, actor),
    )
    return service.summary(row.property_id)


@router.post("/domain-registrations/{registration_id}/verify-dns")
def verify_dns(
    registration_id: int,
    payload: DNSVerificationRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = _execute(
        session,
        lambda: service.verify_dns(registration_id, payload, actor),
    )
    return service.summary(row.property_id)


@router.post("/properties/{property_id}/assets")
def create_asset(
    property_id: str,
    payload: AssetCreateRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    _execute(session, lambda: service.create_asset(property_id, payload))
    return service.summary(property_id)


@router.post("/assets/{asset_id}/review")
def review_asset(
    asset_id: int,
    payload: AssetApprovalRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = _execute(
        session,
        lambda: service.review_asset(asset_id, payload, actor),
    )
    return service.summary(row.property_id)


@router.post("/properties/{property_id}/provider-assignments")
def create_provider_assignment(
    property_id: str,
    payload: ProviderAssignmentInput,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    _execute(
        session,
        lambda: service.create_provider_assignment(property_id, payload, actor),
    )
    return service.summary(property_id)


@router.post("/provider-assignments/{assignment_id}/activate")
def activate_provider(
    assignment_id: int,
    payload: ProviderActivationRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = _execute(
        session,
        lambda: service.activate_provider(assignment_id, payload, actor),
    )
    return service.summary(row.property_id)


@router.post("/properties/{property_id}/provider-assignments/replace")
def replace_provider(
    property_id: str,
    payload: ProviderReplacementRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    _execute(
        session,
        lambda: service.replace_provider(property_id, payload, actor),
    )
    return service.summary(property_id)


@router.post("/properties/{property_id}/site-configs")
def create_site_config(
    property_id: str,
    payload: SiteConfigInput,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    _execute(
        session,
        lambda: service.create_site_config(property_id, payload, actor),
    )
    return service.summary(property_id)


@router.post("/site-configs/{config_id}/review")
def review_site_config(
    config_id: int,
    payload: SiteConfigApprovalRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = _execute(
        session,
        lambda: service.review_site_config(config_id, payload, actor),
    )
    return service.summary(row.property_id)


@router.post("/site-configs/{config_id}/builds")
def create_site_build(
    config_id: int,
    payload: SiteBuildRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = _execute(session, lambda: service.build_site(config_id, payload))
    return service.summary(row.property_id)


@router.post("/site-builds/{build_id}/compliance")
def review_compliance(
    build_id: int,
    payload: ComplianceReviewRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = _execute(
        session,
        lambda: service.review_compliance(build_id, payload, actor),
    )
    return service.summary(row.property_id)


@router.post("/site-builds/{build_id}/deployments")
async def deploy_site(
    build_id: int,
    payload: DeploymentRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    row = await _execute_async(
        session,
        lambda: service.deploy(build_id, payload, actor),
    )
    return service.summary(row.property_id)


@router.post("/properties/{property_id}/deployments/rollback")
async def rollback_site(
    property_id: str,
    payload: RollbackRequest,
    actor: ReviewActor = Depends(review_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PropertyWorkflowService(session)
    await _execute_async(
        session,
        lambda: service.rollback(property_id, payload, actor),
    )
    return service.summary(property_id)
