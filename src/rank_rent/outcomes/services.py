from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from statistics import fmean
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from rank_rent.db.orm import FullOpportunityScoreORM, JsonArtifactORM
from rank_rent.lead_routing.models import AccessContext, LeadAccessRole, TruthBasis
from rank_rent.lead_routing.privacy import redact_pii
from rank_rent.outcomes.interfaces import OutcomeSourceAdapter
from rank_rent.outcomes.models import (
    CalibrationReport,
    CalibrationReportRequest,
    CorrelationResult,
    PropertyDecisionInput,
    PropertyOutcomeRecord,
    ScoringChangeAuthorization,
    ScoringChangeProposal,
)
from rank_rent.outcomes.orm import (
    CalibrationReportORM,
    PropertyDecisionORM,
    PropertyOutcomeORM,
    ScoringChangeReviewORM,
)

REPORT_VERSION = "calibration-v1"


class OutcomeIntegrityError(RuntimeError):
    pass


class AutomaticScoringChangeProhibited(OutcomeIntegrityError):
    pass


class PropertyOutcomeService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record_decision(self, decision: PropertyDecisionInput) -> PropertyDecisionORM:
        existing = self.session.scalar(
            select(PropertyDecisionORM).where(
                PropertyDecisionORM.property_id == decision.property_id
            )
        )
        if existing is not None:
            immutable_values = (
                existing.opportunity_id,
                existing.full_score_id,
                existing.evidence_snapshot_id,
            )
            submitted_values = (
                decision.opportunity_id,
                decision.full_score_id,
                decision.evidence_snapshot_id,
            )
            if immutable_values != submitted_values:
                raise OutcomeIntegrityError("property_decision_is_immutable")
            return existing

        score = self.session.get(FullOpportunityScoreORM, decision.full_score_id)
        if score is None or score.opportunity_id != decision.opportunity_id:
            raise OutcomeIntegrityError("score_does_not_match_opportunity")
        evidence = self.session.get(JsonArtifactORM, decision.evidence_snapshot_id)
        if evidence is None or evidence.opportunity_id != decision.opportunity_id:
            raise OutcomeIntegrityError("evidence_does_not_match_opportunity")
        if evidence.scan_run_id is None:
            raise OutcomeIntegrityError("evidence_is_missing_scan_lineage")
        if evidence.scan_run_id != score.scan_run_id:
            raise OutcomeIntegrityError("score_and_evidence_scan_mismatch")
        if evidence.kind not in {"scan_result", "evidence_snapshot"}:
            raise OutcomeIntegrityError("artifact_is_not_an_evidence_snapshot")
        if evidence.kind == "scan_result" and evidence.payload.get("assessment_type") != "full":
            raise OutcomeIntegrityError("property_decision_requires_full_evidence")
        row = PropertyDecisionORM(
            property_id=decision.property_id,
            opportunity_id=decision.opportunity_id,
            scan_run_id=score.scan_run_id,
            full_score_id=decision.full_score_id,
            evidence_snapshot_id=decision.evidence_snapshot_id,
            score_version_at_selection=score.scoring_version,
            selected_at=decision.selected_at,
            service_family_slug=decision.service_family_slug,
            market_size_band=decision.market_size_band,
            evidence_quality=decision.evidence_quality,
            validated_opportunity_cost_usd=decision.validated_opportunity_cost_usd,
            selection_context={
                "full_score_total": score.total_score,
                "full_score_confidence": score.confidence,
                "scan_run_id": score.scan_run_id,
                "component_scores": score.payload.get("component_scores", {}),
            },
        )
        self.session.add(row)
        self.session.flush()
        return row

    def ingest(self, record: PropertyOutcomeRecord) -> PropertyOutcomeORM:
        decision = self.session.scalar(
            select(PropertyDecisionORM).where(PropertyDecisionORM.property_id == record.property_id)
        )
        if decision is None:
            raise OutcomeIntegrityError("property_decision_not_found")
        if record.period_date < decision.selected_at.date():
            raise OutcomeIntegrityError("outcome_precedes_property_selection")
        if record.indexed_at is not None and record.indexed_at.date() < decision.selected_at.date():
            raise OutcomeIntegrityError("indexing_precedes_property_selection")
        existing = self.session.scalar(
            select(PropertyOutcomeORM).where(
                PropertyOutcomeORM.property_decision_id == decision.id,
                PropertyOutcomeORM.source_name == record.source_name,
                PropertyOutcomeORM.source_record_id == record.source_record_id,
            )
        )
        if existing is not None:
            return existing
        row = PropertyOutcomeORM(
            property_decision_id=decision.id,
            period_date=record.period_date,
            source_type=record.source_type.value,
            source_name=record.source_name,
            source_record_id=record.source_record_id,
            truth_basis=record.truth_basis.value,
            confidence=record.confidence,
            impressions=record.impressions,
            clicks=record.clicks,
            average_position=record.average_position,
            organic_sessions=record.organic_sessions,
            calls=record.calls,
            forms=record.forms,
            qualified_leads=record.qualified_leads,
            appointments=record.appointments,
            won_jobs=record.won_jobs,
            reported_revenue=record.reported_revenue,
            indexed_at=record.indexed_at,
            provider_suitability_score=record.provider_suitability_score,
            addressable_market_score=record.addressable_market_score,
            metadata_payload=redact_pii(record.metadata),
        )
        self.session.add(row)
        self.session.flush()
        return row

    async def collect_from(
        self,
        adapter: OutcomeSourceAdapter,
        *,
        property_id: str,
        start_date: date,
        end_date: date,
    ) -> list[PropertyOutcomeORM]:
        records = await adapter.collect(
            property_id=property_id,
            start_date=start_date,
            end_date=end_date,
        )
        rows: list[PropertyOutcomeORM] = []
        for record in records:
            if record.source_name != adapter.name:
                raise OutcomeIntegrityError("adapter_source_name_mismatch")
            rows.append(self.ingest(record))
        return rows

    def export_property(
        self,
        property_id: str,
        access: AccessContext,
    ) -> dict[str, Any]:
        if access.role not in {
            LeadAccessRole.operator,
            LeadAccessRole.privacy_admin,
            LeadAccessRole.analytics,
        }:
            raise PermissionError("outcome_export_access_denied")
        decision = self.session.scalar(
            select(PropertyDecisionORM).where(PropertyDecisionORM.property_id == property_id)
        )
        if decision is None:
            raise LookupError("property_decision_not_found")
        rows = list(
            self.session.scalars(
                select(PropertyOutcomeORM)
                .where(PropertyOutcomeORM.property_decision_id == decision.id)
                .order_by(PropertyOutcomeORM.period_date, PropertyOutcomeORM.id)
            )
        )
        return {
            "decision": {
                "property_id": decision.property_id,
                "opportunity_id": decision.opportunity_id,
                "scan_run_id": decision.scan_run_id,
                "full_score_id": decision.full_score_id,
                "score_version_at_selection": decision.score_version_at_selection,
                "evidence_snapshot_id": decision.evidence_snapshot_id,
                "selected_at": decision.selected_at.isoformat(),
            },
            "outcomes": [_outcome_payload(row) for row in rows],
        }

    def delete_imported_source(
        self,
        *,
        property_id: str,
        source_name: str,
        access: AccessContext,
    ) -> int:
        if access.role != LeadAccessRole.privacy_admin:
            raise PermissionError("outcome_deletion_requires_privacy_admin")
        decision = self.session.scalar(
            select(PropertyDecisionORM).where(PropertyDecisionORM.property_id == property_id)
        )
        if decision is None:
            return 0
        outcome_ids = list(
            self.session.scalars(
                select(PropertyOutcomeORM.id).where(
                    PropertyOutcomeORM.property_decision_id == decision.id,
                    PropertyOutcomeORM.source_name == source_name,
                )
            )
        )
        self.session.execute(
            delete(PropertyOutcomeORM).where(
                PropertyOutcomeORM.property_decision_id == decision.id,
                PropertyOutcomeORM.source_name == source_name,
            )
        )
        return len(outcome_ids)

    def enforce_retention(
        self,
        *,
        before: date,
        access: AccessContext,
    ) -> int:
        if access.role != LeadAccessRole.privacy_admin:
            raise PermissionError("outcome_retention_requires_privacy_admin")
        outcome_ids = list(
            self.session.scalars(
                select(PropertyOutcomeORM.id).where(PropertyOutcomeORM.period_date < before)
            )
        )
        self.session.execute(
            delete(PropertyOutcomeORM).where(PropertyOutcomeORM.period_date < before)
        )
        return len(outcome_ids)


class CalibrationReportService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def generate(self, request: CalibrationReportRequest) -> CalibrationReport:
        decisions = list(self.session.scalars(select(PropertyDecisionORM)))
        outcome_rows = list(
            self.session.scalars(
                select(PropertyOutcomeORM).where(
                    PropertyOutcomeORM.period_date >= request.start_date,
                    PropertyOutcomeORM.period_date <= request.end_date,
                )
            )
        )
        by_decision: dict[int, list[PropertyOutcomeORM]] = defaultdict(list)
        for row in outcome_rows:
            by_decision[row.property_decision_id].append(row)

        properties: list[dict[str, Any]] = []
        observed_totals = _empty_totals()
        reported_totals = _empty_totals()
        operator_verified_totals = _empty_totals()
        estimated_totals = _empty_totals()
        for decision in decisions:
            rows = by_decision.get(decision.id, [])
            if not rows:
                continue
            for row in rows:
                target = {
                    TruthBasis.observed.value: observed_totals,
                    TruthBasis.provider_reported.value: reported_totals,
                    TruthBasis.operator_verified.value: operator_verified_totals,
                    TruthBasis.estimated.value: estimated_totals,
                }[row.truth_basis]
                _add_totals(target, row)
            verified_rows = [
                row
                for row in rows
                if row.truth_basis
                in {
                    TruthBasis.observed.value,
                    TruthBasis.operator_verified.value,
                }
            ]
            score = float(decision.selection_context.get("full_score_total", 0))
            selected_date = decision.selected_at.date()
            indexed_dates = [row.indexed_at for row in verified_rows if row.indexed_at is not None]
            indexing_days = (
                (min(indexed_dates).date() - selected_date).days if indexed_dates else None
            )
            positions = [
                row.average_position for row in verified_rows if row.average_position is not None
            ]
            ordered = sorted(verified_rows, key=lambda row: row.period_date)
            impression_growth = (
                _growth(
                    float(ordered[0].impressions),
                    float(ordered[-1].impressions),
                )
                if ordered
                else None
            )
            properties.append(
                {
                    "decision": decision,
                    "score": score,
                    "indexing_days": indexing_days,
                    "impression_growth": impression_growth,
                    "top_10": 1.0 if positions and min(positions) <= 10 else 0.0,
                    "lead_volume": float(sum(row.qualified_leads for row in verified_rows)),
                    "won_jobs": float(sum(row.won_jobs for row in verified_rows)),
                    "provider_suitability": _mean_optional(
                        [row.provider_suitability_score for row in rows]
                    ),
                    "addressable_market": _mean_optional(
                        [row.addressable_market_score for row in rows]
                    ),
                }
            )

        correlations = [
            _correlation(
                properties,
                "score_vs_indexing_time",
                "score",
                "indexing_days",
                request.minimum_correlation_sample,
            ),
            _correlation(
                properties,
                "score_vs_impression_growth",
                "score",
                "impression_growth",
                request.minimum_correlation_sample,
            ),
            _correlation(
                properties,
                "score_vs_top_10_achievement",
                "score",
                "top_10",
                request.minimum_correlation_sample,
            ),
            _correlation(
                properties,
                "provider_suitability_vs_tenant_conversion",
                "provider_suitability",
                "won_jobs",
                request.minimum_correlation_sample,
            ),
            _correlation(
                properties,
                "addressable_market_vs_lead_demand",
                "addressable_market",
                "lead_volume",
                request.minimum_correlation_sample,
            ),
        ]
        component_names = sorted(
            {
                str(name)
                for item in properties
                for name in item["decision"].selection_context.get("component_scores", {})
            }
        )
        for component_name in component_names:
            key = f"component:{component_name}"
            for item in properties:
                component_scores = item["decision"].selection_context.get(
                    "component_scores",
                    {},
                )
                item[key] = component_scores.get(component_name)
            correlations.append(
                _correlation(
                    properties,
                    f"component_{component_name}_vs_lead_volume",
                    key,
                    "lead_volume",
                    request.minimum_correlation_sample,
                )
            )
        segment_summaries = {
            "service_family": _segment(properties, "service_family_slug"),
            "market_size": _segment(properties, "market_size_band"),
            "evidence_quality": _segment(properties, "evidence_quality"),
        }
        validated_count = sum(1 for item in properties if item["lead_volume"] > 0)
        total_cost = sum(item["decision"].validated_opportunity_cost_usd for item in properties)
        cost_per_validated = round(total_cost / validated_count, 4) if validated_count else None
        warnings = [
            "Reports are descriptive. Correlation does not establish causation.",
            "No scoring weights were changed or applied by this report.",
        ]
        if any(not result.sufficient_sample for result in correlations):
            warnings.append("One or more comparisons are below the configured minimum sample size.")
        report = CalibrationReport(
            report_version=REPORT_VERSION,
            start_date=request.start_date,
            end_date=request.end_date,
            property_count=len(properties),
            observed_totals=observed_totals,
            reported_totals=reported_totals,
            operator_verified_totals=operator_verified_totals,
            estimated_totals=estimated_totals,
            correlations=correlations,
            segment_summaries=segment_summaries,
            cost_per_validated_opportunity_usd=cost_per_validated,
            warnings=warnings,
            scoring_changes_applied=False,
        )
        stored = CalibrationReportORM(
            report_version=REPORT_VERSION,
            start_date=request.start_date,
            end_date=request.end_date,
            property_count=report.property_count,
            payload=report.model_dump(mode="json"),
            generated_at=report.generated_at,
        )
        self.session.add(stored)
        self.session.flush()
        report.report_id = stored.id
        return report


class ScoringChangeGuard:
    """Authorizes review only; it deliberately has no weight-application capability."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def authorize_manual_review(
        self,
        proposal: ScoringChangeProposal,
    ) -> ScoringChangeAuthorization:
        if proposal.initiated_by.lower() in {"automatic", "system", "calibration_report"}:
            raise AutomaticScoringChangeProhibited("automatic_scoring_changes_are_prohibited")
        if not proposal.benchmark_passed or proposal.benchmark_run_id is None:
            raise OutcomeIntegrityError("passing_benchmark_is_required")
        if proposal.reviewer_id is None:
            raise OutcomeIntegrityError("reviewer_approval_is_required")
        if proposal.current_version == proposal.proposed_version:
            raise OutcomeIntegrityError("proposed_version_must_change")
        row = ScoringChangeReviewORM(
            proposal_id=proposal.proposal_id,
            current_version=proposal.current_version,
            proposed_version=proposal.proposed_version,
            initiated_by=proposal.initiated_by,
            reviewer_id=proposal.reviewer_id,
            benchmark_run_id=proposal.benchmark_run_id,
            benchmark_passed=True,
            rationale=proposal.rationale,
            authorized_for_manual_application=True,
            applied_automatically=False,
        )
        self.session.add(row)
        self.session.flush()
        return ScoringChangeAuthorization(
            proposal_id=proposal.proposal_id,
            authorized_for_manual_application=True,
            reviewer_id=proposal.reviewer_id,
            benchmark_run_id=proposal.benchmark_run_id,
            note="Review recorded. Apply any versioned configuration change outside this service.",
        )


def _empty_totals() -> dict[str, float]:
    return {
        "impressions": 0,
        "clicks": 0,
        "organic_sessions": 0,
        "calls": 0,
        "forms": 0,
        "qualified_leads": 0,
        "appointments": 0,
        "won_jobs": 0,
        "reported_revenue": 0.0,
    }


def _add_totals(target: dict[str, float], row: PropertyOutcomeORM) -> None:
    for key in target:
        target[key] += float(getattr(row, key))


def _growth(first: float, last: float) -> float | None:
    if first <= 0:
        return None if last <= 0 else 1.0
    return (last - first) / first


def _mean_optional(values: list[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return fmean(present) if present else None


def _correlation(
    items: list[dict[str, Any]],
    metric: str,
    x_key: str,
    y_key: str,
    minimum_sample: int,
) -> CorrelationResult:
    pairs = [
        (float(item[x_key]), float(item[y_key]))
        for item in items
        if item.get(x_key) is not None and item.get(y_key) is not None
    ]
    coefficient = _pearson(pairs)
    sufficient = len(pairs) >= minimum_sample
    if not sufficient:
        interpretation = "insufficient_sample"
    elif coefficient is None:
        interpretation = "no_variance"
    else:
        interpretation = "descriptive_correlation_only"
    return CorrelationResult(
        metric=metric,
        sample_size=len(pairs),
        coefficient=round(coefficient, 4) if coefficient is not None else None,
        sufficient_sample=sufficient,
        interpretation=interpretation,
    )


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    x_mean = fmean(xs)
    y_mean = fmean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    x_sum = sum((x - x_mean) ** 2 for x in xs)
    y_sum = sum((y - y_mean) ** 2 for y in ys)
    denominator = math.sqrt(x_sum * y_sum)
    return numerator / denominator if denominator else None


def _segment(items: list[dict[str, Any]], decision_field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        decision = item["decision"]
        groups[str(getattr(decision, decision_field))].append(item)
    return [
        {
            "segment": key,
            "property_count": len(group),
            "average_selection_score": round(fmean(float(item["score"]) for item in group), 3),
            "qualified_leads": int(sum(item["lead_volume"] for item in group)),
            "won_jobs": int(sum(item["won_jobs"] for item in group)),
        }
        for key, group in sorted(groups.items())
    ]


def _outcome_payload(row: PropertyOutcomeORM) -> dict[str, Any]:
    return {
        "period_date": row.period_date.isoformat(),
        "source_type": row.source_type,
        "source_name": row.source_name,
        "source_record_id": row.source_record_id,
        "truth_basis": row.truth_basis,
        "confidence": row.confidence,
        "impressions": row.impressions,
        "clicks": row.clicks,
        "average_position": row.average_position,
        "organic_sessions": row.organic_sessions,
        "calls": row.calls,
        "forms": row.forms,
        "qualified_leads": row.qualified_leads,
        "appointments": row.appointments,
        "won_jobs": row.won_jobs,
        "reported_revenue": row.reported_revenue,
    }
