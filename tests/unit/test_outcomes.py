from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from rank_rent.db.base import Base, make_engine
from rank_rent.db.orm import (
    FullOpportunityScoreORM,
    JsonArtifactORM,
    MarketORM,
    OpportunityORM,
    ScanRunORM,
    ServiceFamilyORM,
)
from rank_rent.lead_routing.models import AccessContext, LeadAccessRole, TruthBasis
from rank_rent.outcomes.adapters import FixtureOutcomeAdapter
from rank_rent.outcomes.models import (
    CalibrationReportRequest,
    OutcomeSourceType,
    PropertyDecisionInput,
    PropertyOutcomeRecord,
    ScoringChangeProposal,
)
from rank_rent.outcomes.orm import (
    PropertyDecisionORM,
    PropertyOutcomeORM,
    ScoringChangeReviewORM,
)
from rank_rent.outcomes.services import (
    AutomaticScoringChangeProhibited,
    CalibrationReportService,
    OutcomeIntegrityError,
    PropertyOutcomeService,
    ScoringChangeGuard,
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
        opportunity = OpportunityORM(
            service_family_id=service.id,
            market_id=market.id,
            status="approved_for_property",
            latest_score=70,
            score_version="score-v2",
        )
        active_session.add(opportunity)
        active_session.flush()
        scan = ScanRunORM(source="fixture", status="completed")
        active_session.add(scan)
        active_session.flush()
        score = FullOpportunityScoreORM(
            scan_run_id=scan.id,
            opportunity_id=opportunity.id,
            scoring_version="score-v2",
            total_score=70,
            confidence="medium",
            explanation="Fixture score.",
            payload={"component_scores": {"demand": 12.0, "providers": 8.0}},
        )
        evidence = JsonArtifactORM(
            opportunity_id=opportunity.id,
            scan_run_id=scan.id,
            kind="scan_result",
            payload={"version": "evidence-v1", "assessment_type": "full"},
        )
        active_session.add_all([score, evidence])
        active_session.commit()
        yield active_session


def _decision(
    session: Session,
    *,
    property_id: str = "property-1",
    offset: int = 0,
) -> PropertyDecisionInput:
    score = session.scalar(select(FullOpportunityScoreORM))
    evidence = session.scalar(select(JsonArtifactORM))
    opportunity = session.scalar(select(OpportunityORM))
    assert score is not None and evidence is not None and opportunity is not None
    return PropertyDecisionInput(
        property_id=property_id,
        opportunity_id=opportunity.id,
        full_score_id=score.id,
        evidence_snapshot_id=evidence.id,
        selected_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=offset),
        service_family_slug="plumbing",
        market_size_band="medium",
        evidence_quality="pass",
        validated_opportunity_cost_usd=2.50,
    )


def _outcome(
    property_id: str,
    index: int,
    *,
    truth_basis: TruthBasis = TruthBasis.observed,
) -> PropertyOutcomeRecord:
    source_type = (
        OutcomeSourceType.provider
        if truth_basis == TruthBasis.provider_reported
        else OutcomeSourceType.search_console
    )
    return PropertyOutcomeRecord(
        property_id=property_id,
        period_date=date(2026, 2, index + 1),
        source_type=source_type,
        source_name=f"fixture-{source_type.value}",
        source_record_id=f"record-{property_id}-{index}-{truth_basis.value}",
        truth_basis=truth_basis,
        confidence="medium",
        impressions=100 * (index + 1),
        clicks=10 * (index + 1),
        average_position=float(15 - index * 2),
        organic_sessions=8 * (index + 1),
        calls=index,
        forms=index + 1,
        qualified_leads=index + 1,
        appointments=index,
        won_jobs=index,
        reported_revenue=500 * index if truth_basis == TruthBasis.provider_reported else 0,
        indexed_at=datetime(2026, 1, index + 2, tzinfo=UTC),
        provider_suitability_score=60 + index * 5,
        addressable_market_score=50 + index * 4,
    )


def test_decision_preserves_original_score_and_evidence(session: Session) -> None:
    service = PropertyOutcomeService(session)
    row = service.record_decision(_decision(session))
    session.commit()

    assert row.score_version_at_selection == "score-v2"
    assert row.selection_context["full_score_total"] == 70
    same = service.record_decision(_decision(session))
    assert same.id == row.id

    conflicting = _decision(session).model_copy(update={"evidence_snapshot_id": 999})
    with pytest.raises(OutcomeIntegrityError, match="property_decision_is_immutable"):
        service.record_decision(conflicting)


def test_decision_rejects_evidence_from_another_scan(session: Session) -> None:
    service = PropertyOutcomeService(session)
    original = _decision(session)
    original_evidence = session.get(JsonArtifactORM, original.evidence_snapshot_id)
    assert original_evidence is not None
    other_scan = ScanRunORM(source="fixture", status="completed")
    session.add(other_scan)
    session.flush()
    other_evidence = JsonArtifactORM(
        opportunity_id=original.opportunity_id,
        scan_run_id=other_scan.id,
        kind="scan_result",
        payload={"assessment_type": "full"},
    )
    session.add(other_evidence)
    session.flush()

    with pytest.raises(OutcomeIntegrityError, match="score_and_evidence_scan_mismatch"):
        service.record_decision(
            original.model_copy(update={"evidence_snapshot_id": other_evidence.id})
        )


def test_database_rejects_property_decision_with_mismatched_scan_lineage(
    session: Session,
) -> None:
    decision = _decision(session)
    score = session.get(FullOpportunityScoreORM, decision.full_score_id)
    assert score is not None
    other_scan = ScanRunORM(source="fixture", status="completed")
    session.add(other_scan)
    session.flush()
    other_evidence = JsonArtifactORM(
        opportunity_id=decision.opportunity_id,
        scan_run_id=other_scan.id,
        kind="scan_result",
        payload={"assessment_type": "full"},
    )
    session.add(other_evidence)
    session.flush()
    session.add(
        PropertyDecisionORM(
            property_id="property-invalid-lineage",
            opportunity_id=decision.opportunity_id,
            scan_run_id=score.scan_run_id,
            full_score_id=score.id,
            evidence_snapshot_id=other_evidence.id,
            score_version_at_selection=score.scoring_version,
            selected_at=decision.selected_at,
            service_family_slug=decision.service_family_slug,
            market_size_band=decision.market_size_band,
            evidence_quality=decision.evidence_quality,
            validated_opportunity_cost_usd=0,
            selection_context={},
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_property_decision_cannot_be_updated_or_deleted_through_orm(session: Session) -> None:
    row = PropertyOutcomeService(session).record_decision(_decision(session))
    session.commit()

    row.evidence_quality = "warn"
    with pytest.raises(ValueError, match="property_decision_is_immutable"):
        session.commit()
    session.rollback()

    stored = session.get(type(row), row.id)
    assert stored is not None
    session.delete(stored)
    with pytest.raises(ValueError, match="property_decision_is_immutable"):
        session.commit()
    session.rollback()


@pytest.mark.asyncio
async def test_fixture_ingestion_is_idempotent_and_source_bound(session: Session) -> None:
    service = PropertyOutcomeService(session)
    service.record_decision(_decision(session))
    record = _outcome("property-1", 1).model_copy(
        update={"metadata": {"customer_email": "lead@example.com", "cohort": "pilot"}}
    )
    adapter = FixtureOutcomeAdapter(record.source_name, [record])

    first = await service.collect_from(
        adapter,
        property_id="property-1",
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 28),
    )
    second = await service.collect_from(
        adapter,
        property_id="property-1",
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 28),
    )
    assert first[0].id == second[0].id
    assert first[0].metadata_payload == {
        "customer_email": "<redacted>",
        "cohort": "pilot",
    }
    assert session.query(PropertyOutcomeORM).count() == 1


def test_outcome_cannot_predate_property_selection(session: Session) -> None:
    service = PropertyOutcomeService(session)
    service.record_decision(_decision(session))
    early = _outcome("property-1", 1).model_copy(update={"period_date": date(2025, 12, 31)})
    with pytest.raises(OutcomeIntegrityError, match="precedes_property_selection"):
        service.ingest(early)


def test_calibration_report_separates_truth_and_never_changes_scoring(
    session: Session,
) -> None:
    outcomes = PropertyOutcomeService(session)
    for index in range(5):
        property_id = f"property-{index}"
        outcomes.record_decision(_decision(session, property_id=property_id, offset=index))
        outcomes.ingest(_outcome(property_id, index))
        outcomes.ingest(
            _outcome(
                property_id,
                index,
                truth_basis=TruthBasis.provider_reported,
            )
        )
    report = CalibrationReportService(session).generate(
        CalibrationReportRequest(
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 28),
            minimum_correlation_sample=5,
        )
    )
    session.commit()

    assert report.property_count == 5
    assert report.observed_totals["impressions"] > 0
    assert report.reported_totals["reported_revenue"] > 0
    assert report.scoring_changes_applied is False
    assert all(item.sufficient_sample for item in report.correlations)
    assert any(item.metric == "component_demand_vs_lead_volume" for item in report.correlations)
    assert report.cost_per_validated_opportunity_usd == 2.5
    assert report.segment_summaries["service_family"][0]["segment"] == "plumbing"
    assert any("Correlation does not establish causation" in item for item in report.warnings)


def test_outcome_export_and_source_deletion_are_access_controlled(
    session: Session,
) -> None:
    service = PropertyOutcomeService(session)
    service.record_decision(_decision(session))
    record = _outcome("property-1", 1)
    service.ingest(record)
    exported = service.export_property(
        "property-1",
        AccessContext(actor_id="analyst", role=LeadAccessRole.analytics),
    )
    assert exported["decision"]["score_version_at_selection"] == "score-v2"
    assert exported["outcomes"][0]["truth_basis"] == "observed"
    with pytest.raises(PermissionError):
        service.delete_imported_source(
            property_id="property-1",
            source_name=record.source_name,
            access=AccessContext(actor_id="analyst", role=LeadAccessRole.analytics),
        )
    deleted = service.delete_imported_source(
        property_id="property-1",
        source_name=record.source_name,
        access=AccessContext(actor_id="privacy", role=LeadAccessRole.privacy_admin),
    )
    assert deleted == 1


def test_outcome_retention_requires_privacy_admin(session: Session) -> None:
    service = PropertyOutcomeService(session)
    service.record_decision(_decision(session))
    service.ingest(_outcome("property-1", 1))
    with pytest.raises(PermissionError):
        service.enforce_retention(
            before=date(2027, 1, 1),
            access=AccessContext(actor_id="analyst", role=LeadAccessRole.analytics),
        )
    deleted = service.enforce_retention(
        before=date(2027, 1, 1),
        access=AccessContext(actor_id="privacy", role=LeadAccessRole.privacy_admin),
    )
    assert deleted == 1


def test_scoring_change_guard_requires_human_and_benchmark(session: Session) -> None:
    guard = ScoringChangeGuard(session)
    automatic = ScoringChangeProposal(
        proposal_id="proposal-auto",
        current_version="v2",
        proposed_version="v3",
        initiated_by="system",
        reviewer_id="reviewer-1",
        benchmark_run_id="benchmark-1",
        benchmark_passed=True,
        rationale="Automatically adjust scoring weights from outcome correlation.",
    )
    with pytest.raises(AutomaticScoringChangeProhibited):
        guard.authorize_manual_review(automatic)

    missing_benchmark = automatic.model_copy(
        update={
            "proposal_id": "proposal-no-benchmark",
            "initiated_by": "operator-1",
            "benchmark_passed": False,
        }
    )
    with pytest.raises(OutcomeIntegrityError, match="passing_benchmark"):
        guard.authorize_manual_review(missing_benchmark)

    approved = automatic.model_copy(
        update={"proposal_id": "proposal-reviewed", "initiated_by": "operator-1"}
    )
    authorization = guard.authorize_manual_review(approved)
    session.commit()
    assert authorization.authorized_for_manual_application is True
    stored = session.scalar(
        select(ScoringChangeReviewORM).where(
            ScoringChangeReviewORM.proposal_id == "proposal-reviewed"
        )
    )
    assert stored is not None
    assert stored.applied_automatically is False


def test_provider_reported_outcome_requires_provider_source() -> None:
    with pytest.raises(ValueError, match="provider source"):
        PropertyOutcomeRecord(
            property_id="property-1",
            period_date=date(2026, 2, 1),
            source_type=OutcomeSourceType.search_console,
            source_name="bad-source",
            source_record_id="bad-record",
            truth_basis=TruthBasis.provider_reported,
            confidence="low",
        )
