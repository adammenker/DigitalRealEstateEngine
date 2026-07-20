from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rank_rent.db.orm import (
    ApiCallORM,
    CompetitorMetricORM,
    FullOpportunityScoreORM,
    KeywordDecisionORM,
    OpportunityORM,
    ProviderCandidateORM,
    ScanRunORM,
    ScoreComponentORM,
    SerpResultORM,
    SerpSnapshotORM,
)
from rank_rent.opportunity_review.models import (
    ApprovalCompleteness,
    BatchConfirmationRequest,
    BatchPlanRequest,
    BatchQueueResult,
    DiscoveryTemplateInput,
    EvidenceOverrideKind,
    EvidenceOverrideRequest,
    EvidenceOverrideReversalRequest,
    EvidencePacket,
    OpportunityState,
    OwnershipRequest,
    ReviewActor,
    ReviewRole,
    ReviewTransitionRequest,
)
from rank_rent.opportunity_review.orm import (
    BatchScanPlanItemORM,
    BatchScanPlanORM,
    DiscoveryTemplateORM,
    EvidenceOverrideORM,
    OpportunityReviewORM,
)
from rank_rent.planning import ScanPlan, build_scan_plan
from rank_rent.repositories import market_from_orm, service_from_orm
from rank_rent.runtime import resolve_data_mode
from rank_rent.services.discovery_report import build_api_cost_ledger
from rank_rent.services.records import save_scan_plan_calls
from rank_rent.settings import get_settings


class OpportunityReviewError(RuntimeError):
    def __init__(self, code: str, detail: Any | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


LEGACY_STATE_MAP = {
    "approved": OpportunityState.approved_for_property,
    "evidence_rejected": OpportunityState.needs_more_evidence,
    "scan_failed": OpportunityState.needs_more_evidence,
    "partial_review": OpportunityState.needs_more_evidence,
    "unusable_review": OpportunityState.needs_more_evidence,
}

ALLOWED_TRANSITIONS: dict[OpportunityState, set[OpportunityState]] = {
    OpportunityState.discovered: {
        OpportunityState.prefilter_review,
        OpportunityState.testing_planned,
        OpportunityState.testing_running,
        OpportunityState.preliminary_review,
        OpportunityState.full_running,
        OpportunityState.full_review,
        OpportunityState.needs_more_evidence,
        OpportunityState.rejected,
        OpportunityState.archived,
    },
    OpportunityState.prefilter_review: {
        OpportunityState.testing_planned,
        OpportunityState.testing_running,
        OpportunityState.full_running,
        OpportunityState.needs_more_evidence,
        OpportunityState.rejected,
        OpportunityState.archived,
    },
    OpportunityState.testing_planned: {
        OpportunityState.testing_running,
        OpportunityState.needs_more_evidence,
        OpportunityState.rejected,
        OpportunityState.archived,
    },
    OpportunityState.testing_running: {
        OpportunityState.preliminary_review,
        OpportunityState.needs_more_evidence,
        OpportunityState.testing_planned,
    },
    OpportunityState.preliminary_review: {
        OpportunityState.testing_planned,
        OpportunityState.full_scan_approved,
        OpportunityState.needs_more_evidence,
        OpportunityState.rejected,
        OpportunityState.archived,
    },
    OpportunityState.full_scan_approved: {
        OpportunityState.full_running,
        OpportunityState.preliminary_review,
        OpportunityState.needs_more_evidence,
        OpportunityState.rejected,
    },
    OpportunityState.full_running: {
        OpportunityState.full_review,
        OpportunityState.needs_more_evidence,
        OpportunityState.full_scan_approved,
    },
    OpportunityState.full_review: {
        OpportunityState.approved_for_property,
        OpportunityState.full_scan_approved,
        OpportunityState.needs_more_evidence,
        OpportunityState.rejected,
        OpportunityState.archived,
    },
    OpportunityState.needs_more_evidence: {
        OpportunityState.testing_planned,
        OpportunityState.testing_running,
        OpportunityState.preliminary_review,
        OpportunityState.full_scan_approved,
        OpportunityState.full_running,
        OpportunityState.full_review,
        OpportunityState.rejected,
        OpportunityState.archived,
    },
    OpportunityState.approved_for_property: {
        OpportunityState.full_running,
        OpportunityState.full_review,
        OpportunityState.needs_more_evidence,
        OpportunityState.archived,
    },
    OpportunityState.rejected: {
        OpportunityState.prefilter_review,
        OpportunityState.preliminary_review,
        OpportunityState.full_review,
        OpportunityState.archived,
    },
    OpportunityState.archived: set(),
}


def canonical_state(value: str) -> OpportunityState:
    mapped = LEGACY_STATE_MAP.get(value)
    if mapped is not None:
        return mapped
    try:
        return OpportunityState(value)
    except ValueError as exc:
        raise OpportunityReviewError("unknown_opportunity_state", {"state": value}) from exc


def require_property_approval(session: Session, opportunity_id: int) -> OpportunityORM:
    opportunity = session.get(OpportunityORM, opportunity_id)
    if opportunity is None:
        raise OpportunityReviewError("opportunity_not_found")
    if canonical_state(opportunity.status) != OpportunityState.approved_for_property:
        raise OpportunityReviewError(
            "property_action_requires_approved_opportunity",
            {
                "opportunity_id": opportunity_id,
                "current_state": opportunity.status,
                "required_state": OpportunityState.approved_for_property.value,
            },
        )
    return opportunity


class OpportunityReviewService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def transition(
        self,
        opportunity_id: int,
        request: ReviewTransitionRequest,
        actor: ReviewActor,
    ) -> OpportunityReviewORM:
        opportunity = self._opportunity(opportunity_id)
        current = canonical_state(opportunity.status)
        self._check_version(opportunity, request.expected_review_version)
        if request.target_state not in ALLOWED_TRANSITIONS[current]:
            raise OpportunityReviewError(
                "invalid_opportunity_transition",
                {
                    "current_state": current.value,
                    "requested_state": request.target_state.value,
                    "allowed_states": sorted(
                        state.value for state in ALLOWED_TRANSITIONS[current]
                    ),
                },
            )
        if request.target_state in {
            OpportunityState.approved_for_property,
            OpportunityState.rejected,
        } and actor.role.value == "system":
            raise OpportunityReviewError("human_reviewer_required")
        if request.target_state == OpportunityState.approved_for_property:
            completeness = self.approval_completeness(opportunity_id)
            if not completeness.complete:
                raise OpportunityReviewError(
                    "approval_evidence_incomplete",
                    completeness.model_dump(mode="json"),
                )
        now = datetime.now(UTC)
        owner = request.owner_user_id or opportunity.owner_user_id
        opportunity.status = request.target_state.value
        opportunity.owner_user_id = owner
        opportunity.review_version += 1
        if request.target_state == OpportunityState.approved_for_property:
            opportunity.approved_at = now
        elif current == OpportunityState.approved_for_property:
            opportunity.approved_at = None
        row = OpportunityReviewORM(
            opportunity_id=opportunity.id,
            prior_state=current.value,
            review_state=request.target_state.value,
            owner_user_id=owner,
            reviewer_user_id=actor.actor_id,
            reviewer_role=actor.role.value,
            decision=request.decision,
            decision_reason=request.decision_reason,
            notes=request.notes,
            tags=request.tags,
            review_version=opportunity.review_version,
            approved_at=now
            if request.target_state == OpportunityState.approved_for_property
            else None,
            rejected_at=now if request.target_state == OpportunityState.rejected else None,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def transition_system(
        self,
        opportunity_id: int,
        target_state: OpportunityState,
        *,
        decision: str,
        reason: str,
    ) -> OpportunityReviewORM | None:
        opportunity = self._opportunity(opportunity_id)
        current = canonical_state(opportunity.status)
        if current == target_state:
            return None
        if target_state not in ALLOWED_TRANSITIONS[current]:
            raise OpportunityReviewError(
                "invalid_system_opportunity_transition",
                {
                    "current_state": current.value,
                    "requested_state": target_state.value,
                },
            )
        return self.transition(
            opportunity_id,
            ReviewTransitionRequest(
                target_state=target_state,
                decision=decision,
                decision_reason=reason,
                owner_user_id=opportunity.owner_user_id,
            ),
            ReviewActor(actor_id="system:scan-pipeline", role=ReviewRole.system),
        )

    def assign_owner(
        self,
        opportunity_id: int,
        request: OwnershipRequest,
        actor: ReviewActor,
    ) -> OpportunityReviewORM:
        opportunity = self._opportunity(opportunity_id)
        self._check_version(opportunity, request.expected_review_version)
        state = canonical_state(opportunity.status)
        opportunity.owner_user_id = request.owner_user_id
        opportunity.review_version += 1
        row = OpportunityReviewORM(
            opportunity_id=opportunity.id,
            prior_state=state.value,
            review_state=state.value,
            owner_user_id=request.owner_user_id,
            reviewer_user_id=actor.actor_id,
            reviewer_role=actor.role.value,
            decision="owner_assigned",
            decision_reason=request.reason,
            notes="",
            tags=["ownership"],
            review_version=opportunity.review_version,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def review_summary(self, opportunity_id: int) -> dict[str, Any]:
        opportunity = self._opportunity(opportunity_id)
        history = list(
            self.session.scalars(
                select(OpportunityReviewORM)
                .where(OpportunityReviewORM.opportunity_id == opportunity_id)
                .order_by(OpportunityReviewORM.id.desc())
            )
        )
        return {
            "opportunity_id": opportunity.id,
            "state": canonical_state(opportunity.status).value,
            "owner_user_id": opportunity.owner_user_id,
            "review_version": opportunity.review_version,
            "approved_at": _iso(opportunity.approved_at),
            "allowed_transitions": sorted(
                state.value
                for state in ALLOWED_TRANSITIONS[canonical_state(opportunity.status)]
            ),
            "approval_completeness": self.approval_completeness(
                opportunity_id
            ).model_dump(mode="json"),
            "history": [_review_payload(row) for row in history],
            "overrides": self.list_overrides(opportunity_id),
        }

    def apply_override(
        self,
        opportunity_id: int,
        request: EvidenceOverrideRequest,
        actor: ReviewActor,
    ) -> EvidenceOverrideORM:
        self._opportunity(opportunity_id)
        original = self._original_evidence_value(opportunity_id, request)
        if (
            request.expected_original_value is not None
            and request.expected_original_value != original
        ):
            raise OpportunityReviewError(
                "evidence_override_original_mismatch",
                {
                    "expected": request.expected_original_value,
                    "actual": original,
                },
            )
        if original == request.new_value:
            raise OpportunityReviewError("evidence_override_has_no_effect")
        active = self._active_override(
            opportunity_id,
            request.override_kind,
            request.target_record_id,
            request.field_name,
        )
        if active is not None:
            raise OpportunityReviewError(
                "active_evidence_override_exists",
                {"override_id": active.id},
            )
        row = EvidenceOverrideORM(
            opportunity_id=opportunity_id,
            override_kind=request.override_kind.value,
            target_record_id=request.target_record_id,
            field_name=request.field_name,
            action="apply",
            original_value=original,
            new_value=request.new_value,
            actor_user_id=actor.actor_id,
            actor_role=actor.role.value,
            reason=request.reason,
            score_impact=request.score_impact,
            score_impact_explanation=request.score_impact_explanation,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def reverse_override(
        self,
        opportunity_id: int,
        override_id: int,
        request: EvidenceOverrideReversalRequest,
        actor: ReviewActor,
    ) -> EvidenceOverrideORM:
        original = self.session.get(EvidenceOverrideORM, override_id)
        if (
            original is None
            or original.opportunity_id != opportunity_id
            or original.action != "apply"
        ):
            raise OpportunityReviewError("evidence_override_not_found")
        reversed_already = self.session.scalar(
            select(EvidenceOverrideORM).where(
                EvidenceOverrideORM.reverses_override_id == override_id
            )
        )
        if reversed_already is not None:
            raise OpportunityReviewError(
                "evidence_override_already_reversed",
                {"reversal_id": reversed_already.id},
            )
        row = EvidenceOverrideORM(
            opportunity_id=opportunity_id,
            override_kind=original.override_kind,
            target_record_id=original.target_record_id,
            field_name=original.field_name,
            action="revert",
            original_value=original.new_value,
            new_value=original.original_value,
            actor_user_id=actor.actor_id,
            actor_role=actor.role.value,
            reason=request.reason,
            score_impact=request.score_impact,
            score_impact_explanation=request.score_impact_explanation,
            reverses_override_id=original.id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_overrides(self, opportunity_id: int) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(EvidenceOverrideORM)
                .where(EvidenceOverrideORM.opportunity_id == opportunity_id)
                .order_by(EvidenceOverrideORM.id)
            )
        )
        reversed_ids = {
            row.reverses_override_id
            for row in rows
            if row.reverses_override_id is not None
        }
        return [
            {
                **_override_payload(row),
                "active": row.action == "apply" and row.id not in reversed_ids,
            }
            for row in rows
        ]

    def create_template(
        self,
        request: DiscoveryTemplateInput,
        actor: ReviewActor,
    ) -> DiscoveryTemplateORM:
        from rank_rent.db.orm import ServiceFamilyORM

        if self.session.get(ServiceFamilyORM, request.service_family_id) is None:
            raise OpportunityReviewError("service_family_not_found")
        existing = self.session.scalar(
            select(DiscoveryTemplateORM).where(
                DiscoveryTemplateORM.owner_user_id == actor.actor_id,
                DiscoveryTemplateORM.name == request.name,
            )
        )
        if existing is not None:
            raise OpportunityReviewError("discovery_template_name_exists")
        row = DiscoveryTemplateORM(
            name=request.name,
            owner_user_id=actor.actor_id,
            service_family_id=request.service_family_id,
            market_filters=request.market_filters,
            prefilter_profile=request.prefilter_profile,
            testing_profile=request.testing_profile,
            full_profile=request.full_profile,
            budget_usd=float(request.budget_usd),
            freshness_requirements=request.freshness_requirements,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def update_template(
        self,
        template_id: int,
        request: DiscoveryTemplateInput,
        actor: ReviewActor,
    ) -> DiscoveryTemplateORM:
        from rank_rent.db.orm import ServiceFamilyORM

        row = self._owned_template(template_id, actor)
        if self.session.get(ServiceFamilyORM, request.service_family_id) is None:
            raise OpportunityReviewError("service_family_not_found")
        row.name = request.name
        row.service_family_id = request.service_family_id
        row.market_filters = request.market_filters
        row.prefilter_profile = request.prefilter_profile
        row.testing_profile = request.testing_profile
        row.full_profile = request.full_profile
        row.budget_usd = float(request.budget_usd)
        row.freshness_requirements = request.freshness_requirements
        self.session.flush()
        return row

    def archive_template(self, template_id: int, actor: ReviewActor) -> DiscoveryTemplateORM:
        row = self._owned_template(template_id, actor)
        row.active = False
        self.session.flush()
        return row

    def list_templates(self, actor: ReviewActor) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(DiscoveryTemplateORM)
                .where(
                    DiscoveryTemplateORM.owner_user_id == actor.actor_id,
                    DiscoveryTemplateORM.active.is_(True),
                )
                .order_by(DiscoveryTemplateORM.name)
            )
        )
        return [_template_payload(row) for row in rows]

    def create_batch_plan(
        self,
        request: BatchPlanRequest,
        actor: ReviewActor,
    ) -> BatchScanPlanORM:
        settings = get_settings()
        template: DiscoveryTemplateORM | None = None
        if request.template_id is not None:
            template = self._owned_template(request.template_id, actor)
            if Decimal(str(template.budget_usd)) < request.aggregate_budget_usd:
                raise OpportunityReviewError("batch_budget_exceeds_template_budget")
        rows = list(
            self.session.scalars(
                select(OpportunityORM)
                .where(OpportunityORM.id.in_(request.opportunity_ids))
                .order_by(OpportunityORM.id)
            )
        )
        if len(rows) != len(request.opportunity_ids):
            found = {row.id for row in rows}
            raise OpportunityReviewError(
                "batch_opportunity_not_found",
                {"missing_ids": sorted(set(request.opportunity_ids) - found)},
            )
        plans: list[tuple[OpportunityORM, ScanPlan]] = []
        total = Decimal("0")
        mode = resolve_data_mode(request.data_mode)
        for opportunity in rows:
            if (
                template is not None
                and opportunity.service_family_id != template.service_family_id
            ):
                raise OpportunityReviewError(
                    "batch_opportunity_service_does_not_match_template",
                    {"opportunity_id": opportunity.id},
                )
            state = canonical_state(opportunity.status)
            allowed = (
                {
                    OpportunityState.discovered,
                    OpportunityState.prefilter_review,
                    OpportunityState.preliminary_review,
                    OpportunityState.needs_more_evidence,
                }
                if request.scan_profile == "testing"
                else {
                    OpportunityState.preliminary_review,
                    OpportunityState.needs_more_evidence,
                }
            )
            if state not in allowed:
                raise OpportunityReviewError(
                    "opportunity_not_eligible_for_batch_plan",
                    {
                        "opportunity_id": opportunity.id,
                        "state": state.value,
                        "scan_profile": request.scan_profile,
                    },
                )
            plan = build_scan_plan(
                settings,
                mode,
                service_from_orm(opportunity.service_family),
                market_from_orm(opportunity.market),
                session=self.session,
                scan_profile=request.scan_profile,
            )
            if plan.blocked:
                raise OpportunityReviewError(
                    "batch_item_blocked_by_scan_policy",
                    {"opportunity_id": opportunity.id, "reason": plan.block_reason},
                )
            total += plan.estimated_uncached_cost_usd
            plans.append((opportunity, plan))
        if total > request.aggregate_budget_usd:
            raise OpportunityReviewError(
                "batch_aggregate_cost_exceeds_budget",
                {
                    "estimated_cost_usd": str(total),
                    "aggregate_budget_usd": str(request.aggregate_budget_usd),
                },
            )
        batch = BatchScanPlanORM(
            name=request.name,
            template_id=request.template_id,
            created_by=actor.actor_id,
            scan_profile=request.scan_profile,
            data_mode=request.data_mode,
            aggregate_budget_usd=float(request.aggregate_budget_usd),
            aggregate_estimated_cost_usd=float(total),
        )
        self.session.add(batch)
        self.session.flush()
        for opportunity, plan in plans:
            self.session.add(
                BatchScanPlanItemORM(
                    batch_plan_id=batch.id,
                    opportunity_id=opportunity.id,
                    estimated_cost_usd=float(plan.estimated_uncached_cost_usd),
                    scan_plan_payload=plan.model_dump(mode="json"),
                )
            )
            if request.scan_profile == "testing":
                self.transition_system(
                    opportunity.id,
                    OpportunityState.testing_planned,
                    decision="batch_testing_planned",
                    reason=f"Included in bounded testing batch {batch.id}.",
                )
        self.session.flush()
        return batch

    def confirm_batch(
        self,
        batch_id: int,
        request: BatchConfirmationRequest,
        actor: ReviewActor,
    ) -> BatchScanPlanORM:
        batch = self._batch(batch_id)
        if batch.status != "planned":
            raise OpportunityReviewError("batch_plan_not_awaiting_confirmation")
        estimated = Decimal(str(batch.aggregate_estimated_cost_usd))
        budget = Decimal(str(batch.aggregate_budget_usd))
        if request.approved_max_cost_usd < estimated:
            raise OpportunityReviewError("approved_batch_cost_below_estimate")
        if request.approved_max_cost_usd > budget:
            raise OpportunityReviewError("approved_batch_cost_exceeds_plan_budget")
        now = datetime.now(UTC)
        batch.status = "confirmed"
        batch.approved_max_cost_usd = float(request.approved_max_cost_usd)
        batch.confirmed_by = actor.actor_id
        batch.confirmation_reason = request.reason
        batch.confirmed_at = now
        if batch.scan_profile == "full":
            for item in self._batch_items(batch.id):
                self.transition(
                    item.opportunity_id,
                    ReviewTransitionRequest(
                        target_state=OpportunityState.full_scan_approved,
                        decision="bounded_batch_full_scan_approval",
                        decision_reason=request.reason,
                    ),
                    actor,
                )
        self.session.flush()
        return batch

    def queue_batch(self, batch_id: int, actor: ReviewActor) -> BatchQueueResult:
        batch = self._batch(batch_id)
        if batch.status != "confirmed" or batch.approved_max_cost_usd is None:
            raise OpportunityReviewError("batch_plan_requires_confirmation")
        items = self._batch_items(batch.id)
        planned_total = sum(
            (Decimal(str(item.estimated_cost_usd)) for item in items),
            Decimal("0"),
        )
        approved = Decimal(str(batch.approved_max_cost_usd))
        if planned_total > approved:
            raise OpportunityReviewError("batch_plan_exceeds_confirmed_cost_bound")
        active_opportunity_id = self.session.scalar(
            select(ScanRunORM.opportunity_id)
            .where(
                ScanRunORM.opportunity_id.in_([item.opportunity_id for item in items]),
                ScanRunORM.status.in_(["queued", "running"]),
            )
            .limit(1)
        )
        if active_opportunity_id is not None:
            raise OpportunityReviewError(
                "batch_opportunity_already_has_active_scan",
                {"opportunity_id": active_opportunity_id},
            )
        queued: list[int] = []
        for item in items:
            opportunity = self._opportunity(item.opportunity_id)
            plan = ScanPlan.model_validate(item.scan_plan_payload)
            service = service_from_orm(opportunity.service_family)
            market = market_from_orm(opportunity.market)
            scan = ScanRunORM(
                opportunity_id=opportunity.id,
                source="batch_review_async",
                status="queued",
                estimated_cost_usd=item.estimated_cost_usd,
                planned_cost_usd=item.estimated_cost_usd,
                data_mode=batch.data_mode,
                scan_profile=batch.scan_profile,
                progress_stage="queued",
                cache_policy_version="v2",
                integration_versions={
                    "data_mode": batch.data_mode,
                    "queued": True,
                    "batch_plan_id": batch.id,
                },
                request_parameters={
                    "service": service.slug,
                    "market": market.slug,
                    "data_mode": batch.data_mode,
                    "scan_profile": batch.scan_profile,
                    "scan_plan": item.scan_plan_payload,
                    "service_payload": service.model_dump(mode="json"),
                    "market_payload": market.model_dump(mode="json"),
                    "final_market_payload": market.model_dump(mode="json"),
                    "batch_plan_id": batch.id,
                    "batch_confirmed_by": batch.confirmed_by,
                    "batch_approved_max_cost_usd": batch.approved_max_cost_usd,
                },
            )
            self.session.add(scan)
            self.session.flush()
            save_scan_plan_calls(self.session, scan.id, plan)
            item.scan_run_id = scan.id
            item.status = "queued"
            queued.append(scan.id)
        batch.status = "queued"
        batch.queued_at = datetime.now(UTC)
        self.session.flush()
        return BatchQueueResult(
            batch_plan_id=batch.id,
            queued_scan_ids=queued,
            aggregate_planned_cost_usd=planned_total,
            approved_max_cost_usd=approved,
        )

    def batch_payload(self, batch_id: int) -> dict[str, Any]:
        batch = self._batch(batch_id)
        return {
            "id": batch.id,
            "name": batch.name,
            "template_id": batch.template_id,
            "created_by": batch.created_by,
            "scan_profile": batch.scan_profile,
            "data_mode": batch.data_mode,
            "status": batch.status,
            "aggregate_budget_usd": batch.aggregate_budget_usd,
            "aggregate_estimated_cost_usd": batch.aggregate_estimated_cost_usd,
            "approved_max_cost_usd": batch.approved_max_cost_usd,
            "confirmed_by": batch.confirmed_by,
            "confirmation_reason": batch.confirmation_reason,
            "confirmed_at": _iso(batch.confirmed_at),
            "queued_at": _iso(batch.queued_at),
            "items": [
                {
                    "id": item.id,
                    "opportunity_id": item.opportunity_id,
                    "status": item.status,
                    "estimated_cost_usd": item.estimated_cost_usd,
                    "scan_run_id": item.scan_run_id,
                    "scan_plan": item.scan_plan_payload,
                }
                for item in self._batch_items(batch.id)
            ],
        }

    def approval_completeness(
        self,
        opportunity_id: int,
        *,
        maximum_age_days: int = 90,
    ) -> ApprovalCompleteness:
        opportunity = self._opportunity(opportunity_id)
        scan = self.session.scalar(
            select(ScanRunORM)
            .where(
                ScanRunORM.opportunity_id == opportunity_id,
                ScanRunORM.scan_profile == "full",
                ScanRunORM.status == "completed",
            )
            .order_by(ScanRunORM.completed_at.desc(), ScanRunORM.id.desc())
            .limit(1)
        )
        score = (
            self.session.scalar(
                select(FullOpportunityScoreORM)
                .where(
                    FullOpportunityScoreORM.opportunity_id == opportunity_id,
                    FullOpportunityScoreORM.scan_run_id == scan.id,
                )
                .order_by(FullOpportunityScoreORM.created_at.desc())
                .limit(1)
            )
            if scan is not None
            else None
        )
        quality = (scan.partial_outputs or {}).get("evidence_quality", {}) if scan else {}
        quality_status = quality.get("status") if isinstance(quality, dict) else None
        score_evidence_status = (
            score.payload.get("evidence_status", "complete") if score else None
        )
        cost_ledger = build_api_cost_ledger(self.session, scan.id) if scan else {}
        cutoff = datetime.now(UTC) - timedelta(days=maximum_age_days)
        completed_at = _aware(scan.completed_at) if scan and scan.completed_at else None
        counts = self._evidence_counts(scan.id) if scan else {}
        checks = {
            "owner_assigned": bool(opportunity.owner_user_id),
            "completed_full_scan": scan is not None,
            "full_score_matches_scan": score is not None,
            "full_score_is_complete": score_evidence_status == "complete",
            "evidence_quality_passes": quality_status in {"pass", "warning"},
            "keyword_decisions_present": counts.get("keyword_decisions", 0) > 0,
            "serp_snapshots_present": counts.get("serp_snapshots", 0) > 0,
            "competitor_metrics_present": counts.get("competitor_metrics", 0) > 0,
            "provider_candidates_present": counts.get("provider_candidates", 0) > 0,
            "cost_ledger_complete": bool(cost_ledger.get("ledger_complete")),
            "evidence_is_fresh": bool(completed_at and completed_at >= cutoff),
        }
        failure_labels = {
            "owner_assigned": "Assign an opportunity owner.",
            "completed_full_scan": "A completed full scan is required.",
            "full_score_matches_scan": "The completed full scan needs a matching full score.",
            "full_score_is_complete": "The full score is partial or unusable.",
            "evidence_quality_passes": "Evidence quality must pass or contain warnings only.",
            "keyword_decisions_present": "Keyword decisions are missing.",
            "serp_snapshots_present": "SERP evidence is missing.",
            "competitor_metrics_present": "Competitor evidence is missing.",
            "provider_candidates_present": "Provider evidence is missing.",
            "cost_ledger_complete": "The API cost ledger is incomplete.",
            "evidence_is_fresh": f"Full evidence must be no more than {maximum_age_days} days old.",
        }
        failures = [failure_labels[key] for key, passed in checks.items() if not passed]
        warnings = []
        if opportunity.missing_data_flags:
            warnings.append(
                "Score reports missing data: "
                + ", ".join(sorted(opportunity.missing_data_flags))
            )
        if quality_status == "warning":
            warnings.append("Evidence quality passed with warnings.")
        return ApprovalCompleteness(
            complete=not failures,
            full_scan_run_id=scan.id if scan else None,
            full_score_id=score.id if score else None,
            checks=checks,
            failures=failures,
            warnings=warnings,
        )

    def evidence_packet(
        self,
        opportunity_id: int,
        actor: ReviewActor,
    ) -> EvidencePacket:
        opportunity = self._opportunity(opportunity_id)
        scan = self.session.scalar(
            select(ScanRunORM)
            .where(ScanRunORM.opportunity_id == opportunity_id)
            .order_by(
                (ScanRunORM.scan_profile == "full").desc(),
                ScanRunORM.completed_at.desc(),
                ScanRunORM.id.desc(),
            )
            .limit(1)
        )
        if scan is None:
            raise OpportunityReviewError("opportunity_has_no_scan_evidence")
        score = self.session.scalar(
            select(FullOpportunityScoreORM)
            .where(
                FullOpportunityScoreORM.opportunity_id == opportunity_id,
                FullOpportunityScoreORM.scan_run_id == scan.id,
            )
            .order_by(FullOpportunityScoreORM.created_at.desc())
            .limit(1)
        )
        reviews = list(
            self.session.scalars(
                select(OpportunityReviewORM)
                .where(OpportunityReviewORM.opportunity_id == opportunity_id)
                .order_by(OpportunityReviewORM.id)
            )
        )
        keyword_rows = list(
            self.session.scalars(
                select(KeywordDecisionORM)
                .where(KeywordDecisionORM.scan_run_id == scan.id)
                .order_by(KeywordDecisionORM.rank, KeywordDecisionORM.id)
            )
        )
        snapshots = list(
            self.session.scalars(
                select(SerpSnapshotORM)
                .where(SerpSnapshotORM.scan_run_id == scan.id)
                .order_by(SerpSnapshotORM.id)
            )
        )
        snapshot_ids = [row.id for row in snapshots]
        result_rows = (
            list(
                self.session.scalars(
                    select(SerpResultORM)
                    .where(SerpResultORM.serp_snapshot_id.in_(snapshot_ids))
                    .order_by(SerpResultORM.serp_snapshot_id, SerpResultORM.order)
                )
            )
            if snapshot_ids
            else []
        )
        results_by_snapshot: dict[int, list[dict[str, Any]]] = {}
        for result in result_rows:
            results_by_snapshot.setdefault(result.serp_snapshot_id, []).append(
                {
                    "id": result.id,
                    "position": result.order,
                    "url": result.url,
                    "domain": result.domain,
                    "title": result.title,
                    "classification": result.classification,
                    "classification_confidence": result.classification_confidence,
                    "matched_rules": result.matched_rules,
                }
            )
        competitors = list(
            self.session.scalars(
                select(CompetitorMetricORM)
                .where(CompetitorMetricORM.scan_run_id == scan.id)
                .order_by(CompetitorMetricORM.id)
            )
        )
        providers = list(
            self.session.scalars(
                select(ProviderCandidateORM)
                .where(ProviderCandidateORM.scan_run_id == scan.id)
                .order_by(ProviderCandidateORM.suitability_score.desc(), ProviderCandidateORM.id)
            )
        )
        components = list(
            self.session.scalars(
                select(ScoreComponentORM)
                .where(ScoreComponentORM.scan_run_id == scan.id)
                .order_by(ScoreComponentORM.id)
            )
        )
        calls = list(
            self.session.scalars(
                select(ApiCallORM)
                .where(ApiCallORM.scan_run_id == scan.id)
                .order_by(ApiCallORM.id)
            )
        )
        request = scan.request_parameters or {}
        return EvidencePacket(
            generated_by=actor.actor_id,
            opportunity={
                "id": opportunity.id,
                "service": opportunity.service_family.display_name,
                "market": opportunity.market.display_name,
                "state": canonical_state(opportunity.status).value,
                "owner_user_id": opportunity.owner_user_id,
                "latest_score": opportunity.latest_score,
                "score_version": opportunity.score_version,
            },
            review={
                "review_version": opportunity.review_version,
                "approval_completeness": self.approval_completeness(
                    opportunity_id
                ).model_dump(mode="json"),
            },
            market_evidence={
                "market": request.get("final_market_payload")
                or request.get("market_payload"),
                "public_data_prefilter": request.get("public_data_prefilter"),
            },
            keyword_decisions=[
                {
                    "id": row.id,
                    "keyword": row.keyword,
                    "decision": row.decision,
                    "reason": row.reason,
                    "rank": row.rank,
                    "representative": row.representative,
                    "intent": row.intent,
                    "search_volume": row.search_volume,
                    "cpc": row.cpc,
                    "granularity": row.granularity,
                }
                for row in keyword_rows
            ],
            serps=[
                {
                    "id": row.id,
                    "query": row.query,
                    "market_id": row.market_id,
                    "device": row.device,
                    "captured_at": _iso(row.captured_at),
                    "features_present": row.features_present,
                    "results": results_by_snapshot.get(row.id, []),
                }
                for row in snapshots
            ],
            competitors=[
                {
                    "id": row.id,
                    "domain": row.domain,
                    "url": row.url,
                    "referring_domains": row.referring_domains,
                    "authority": row.authority,
                    "page_relevance_score": row.page_relevance_score,
                    "local_relevance": row.local_relevance,
                    "page_type": row.page_type,
                    "representative_query": row.representative_query,
                    "serp_position": row.serp_position,
                    "serp_observations": row.serp_observations,
                }
                for row in competitors
            ],
            providers=[
                {
                    "id": row.id,
                    "name": row.name,
                    "website": row.website,
                    "address": row.address,
                    "categories": row.categories,
                    "business_status": row.business_status,
                    "suitability_score": row.suitability_score,
                    "suitability_reasons": row.suitability_reasons,
                    "source_timestamp": _iso(row.source_timestamp),
                }
                for row in providers
            ],
            score_trace={
                "full_score_id": score.id if score else None,
                "score": score.payload if score else None,
                "components": [
                    {
                        "component": row.component,
                        "score": row.score,
                        "inputs": row.inputs,
                        "formula": row.formula,
                        "penalties": row.penalties,
                    }
                    for row in components
                ],
            },
            confidence=score.confidence if score else opportunity.confidence,
            costs={
                "planned_cost_usd": scan.planned_cost_usd,
                "actual_cost_usd": scan.actual_cost_usd,
                "calls": [
                    {
                        "id": row.id,
                        "planned_request_id": row.planned_request_id,
                        "endpoint": row.endpoint,
                        "stage": row.stage,
                        "cache_hit": row.cache_hit,
                        "estimated_cost_usd": row.estimated_cost_usd,
                        "actual_cost_usd": row.actual_cost_usd,
                        "status": row.status,
                    }
                    for row in calls
                ],
            },
            freshness={
                "scan_completed_at": _iso(scan.completed_at),
                "scan_age_days": (
                    (datetime.now(UTC) - _aware(scan.completed_at)).days
                    if scan.completed_at
                    else None
                ),
                "provider_source_timestamps": [
                    _iso(row.source_timestamp) for row in providers
                ],
                "serp_captured_at": [_iso(row.captured_at) for row in snapshots],
            },
            overrides=self.list_overrides(opportunity_id),
            review_notes=[_review_payload(row) for row in reviews],
        )

    def evidence_packet_csv(self, packet: EvidencePacket) -> str:
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["section", "record_index", "payload_json"],
            lineterminator="\n",
        )
        writer.writeheader()
        payload = packet.model_dump(mode="json")
        for section, value in payload.items():
            records = value if isinstance(value, list) else [value]
            for index, record in enumerate(records):
                writer.writerow(
                    {
                        "section": section,
                        "record_index": index,
                        "payload_json": json.dumps(
                            record,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    }
                )
        return output.getvalue()

    def _original_evidence_value(
        self,
        opportunity_id: int,
        request: EvidenceOverrideRequest,
    ) -> Any:
        if request.override_kind == EvidenceOverrideKind.serp_classification:
            if request.field_name != "classification":
                raise OpportunityReviewError("unsupported_serp_override_field")
            row = self.session.scalar(
                select(SerpResultORM)
                .join(
                    SerpSnapshotORM,
                    SerpSnapshotORM.id == SerpResultORM.serp_snapshot_id,
                )
                .where(
                    SerpResultORM.id == request.target_record_id,
                    SerpSnapshotORM.opportunity_id == opportunity_id,
                )
            )
            if row is None:
                raise OpportunityReviewError("serp_result_not_found")
            return row.classification
        if request.override_kind == EvidenceOverrideKind.provider_suitability:
            if request.field_name not in {
                "suitability_score",
                "business_status",
                "categories",
            }:
                raise OpportunityReviewError("unsupported_provider_override_field")
            provider_row = self.session.scalar(
                select(ProviderCandidateORM).where(
                    ProviderCandidateORM.id == request.target_record_id,
                    ProviderCandidateORM.opportunity_id == opportunity_id,
                )
            )
            if provider_row is None:
                raise OpportunityReviewError("provider_candidate_not_found")
            return getattr(provider_row, request.field_name)
        if request.override_kind == EvidenceOverrideKind.geographic_interpretation:
            allowed_fields = {
                "display_name",
                "state",
                "county",
                "metro",
                "latitude",
                "longitude",
                "boundary_radius_km",
                "geography_id",
            }
            if request.field_name not in allowed_fields:
                raise OpportunityReviewError("unsupported_geography_override_field")
            opportunity = self._opportunity(opportunity_id)
            if opportunity.market_id != request.target_record_id:
                raise OpportunityReviewError("market_does_not_match_opportunity")
            return getattr(opportunity.market, request.field_name)
        if request.override_kind == EvidenceOverrideKind.data_quality_warning:
            scan = self.session.get(ScanRunORM, request.target_record_id)
            if scan is None or scan.opportunity_id != opportunity_id:
                raise OpportunityReviewError("scan_run_not_found")
            quality = (scan.partial_outputs or {}).get("evidence_quality", {})
            warnings = quality.get("warnings", []) if isinstance(quality, dict) else []
            errors = quality.get("errors", []) if isinstance(quality, dict) else []
            return request.field_name in [*warnings, *errors]
        raise OpportunityReviewError("unsupported_evidence_override_kind")

    def _active_override(
        self,
        opportunity_id: int,
        kind: EvidenceOverrideKind,
        target_record_id: int,
        field_name: str,
    ) -> EvidenceOverrideORM | None:
        candidates = list(
            self.session.scalars(
                select(EvidenceOverrideORM)
                .where(
                    EvidenceOverrideORM.opportunity_id == opportunity_id,
                    EvidenceOverrideORM.override_kind == kind.value,
                    EvidenceOverrideORM.target_record_id == target_record_id,
                    EvidenceOverrideORM.field_name == field_name,
                    EvidenceOverrideORM.action == "apply",
                )
                .order_by(EvidenceOverrideORM.id.desc())
            )
        )
        for candidate in candidates:
            reversed_row = self.session.scalar(
                select(EvidenceOverrideORM.id).where(
                    EvidenceOverrideORM.reverses_override_id == candidate.id
                )
            )
            if reversed_row is None:
                return candidate
        return None

    def _evidence_counts(self, scan_id: int) -> dict[str, int]:
        return {
            "keyword_decisions": self._count(KeywordDecisionORM, scan_id),
            "serp_snapshots": self._count(SerpSnapshotORM, scan_id),
            "competitor_metrics": self._count(CompetitorMetricORM, scan_id),
            "provider_candidates": self._count(ProviderCandidateORM, scan_id),
        }

    def _count(self, model: Any, scan_id: int) -> int:
        return len(
            list(
                self.session.scalars(
                    select(model.id).where(model.scan_run_id == scan_id)
                )
            )
        )

    def _check_version(
        self,
        opportunity: OpportunityORM,
        expected: int | None,
    ) -> None:
        if expected is not None and opportunity.review_version != expected:
            raise OpportunityReviewError(
                "opportunity_review_version_conflict",
                {
                    "expected": expected,
                    "actual": opportunity.review_version,
                },
            )

    def _opportunity(self, opportunity_id: int) -> OpportunityORM:
        opportunity = self.session.get(OpportunityORM, opportunity_id)
        if opportunity is None:
            raise OpportunityReviewError("opportunity_not_found")
        return opportunity

    def _owned_template(
        self,
        template_id: int,
        actor: ReviewActor,
    ) -> DiscoveryTemplateORM:
        row = self.session.get(DiscoveryTemplateORM, template_id)
        if row is None:
            raise OpportunityReviewError("discovery_template_not_found")
        if row.owner_user_id != actor.actor_id and actor.role.value != "admin":
            raise OpportunityReviewError("discovery_template_access_denied")
        if not row.active:
            raise OpportunityReviewError("discovery_template_is_archived")
        return row

    def _batch(self, batch_id: int) -> BatchScanPlanORM:
        row = self.session.get(BatchScanPlanORM, batch_id)
        if row is None:
            raise OpportunityReviewError("batch_plan_not_found")
        return row

    def _batch_items(self, batch_id: int) -> list[BatchScanPlanItemORM]:
        return list(
            self.session.scalars(
                select(BatchScanPlanItemORM)
                .where(BatchScanPlanItemORM.batch_plan_id == batch_id)
                .order_by(BatchScanPlanItemORM.id)
            )
        )


def _review_payload(row: OpportunityReviewORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "opportunity_id": row.opportunity_id,
        "prior_state": row.prior_state,
        "review_state": row.review_state,
        "owner_user_id": row.owner_user_id,
        "reviewer_user_id": row.reviewer_user_id,
        "reviewer_role": row.reviewer_role,
        "decision": row.decision,
        "decision_reason": row.decision_reason,
        "notes": row.notes,
        "tags": row.tags,
        "review_version": row.review_version,
        "approved_at": _iso(row.approved_at),
        "rejected_at": _iso(row.rejected_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _override_payload(row: EvidenceOverrideORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "opportunity_id": row.opportunity_id,
        "override_kind": row.override_kind,
        "target_record_id": row.target_record_id,
        "field_name": row.field_name,
        "action": row.action,
        "original_value": row.original_value,
        "new_value": row.new_value,
        "actor_user_id": row.actor_user_id,
        "actor_role": row.actor_role,
        "reason": row.reason,
        "score_impact": row.score_impact,
        "score_impact_explanation": row.score_impact_explanation,
        "reverses_override_id": row.reverses_override_id,
        "created_at": _iso(row.created_at),
    }


def _template_payload(row: DiscoveryTemplateORM) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "owner_user_id": row.owner_user_id,
        "service_family_id": row.service_family_id,
        "market_filters": row.market_filters,
        "prefilter_profile": row.prefilter_profile,
        "testing_profile": row.testing_profile,
        "full_profile": row.full_profile,
        "budget_usd": row.budget_usd,
        "freshness_requirements": row.freshness_requirements,
        "active": row.active,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value is not None else None
