from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from rank_rent.db.base import Base, get_session, make_engine
from rank_rent.db.orm import (
    CompetitorMetricORM,
    FullOpportunityScoreORM,
    KeywordDecisionORM,
    MarketORM,
    OpportunityORM,
    ProviderCandidateORM,
    ScanRunORM,
    ScoreComponentORM,
    SerpResultORM,
    SerpSnapshotORM,
    ServiceFamilyORM,
)
from rank_rent.lead_routing.services import ProviderAssignmentError, ProviderOperationsService
from rank_rent.main import app
from rank_rent.opportunity_review.models import (
    BatchConfirmationRequest,
    BatchPlanRequest,
    DiscoveryTemplateInput,
    EvidenceOverrideKind,
    EvidenceOverrideRequest,
    EvidenceOverrideReversalRequest,
    OpportunityState,
    OwnershipRequest,
    ReviewActor,
    ReviewRole,
    ReviewTransitionRequest,
)
from rank_rent.opportunity_review.orm import (
    BatchScanPlanORM,
    EvidenceOverrideORM,
    OpportunityReviewORM,
)
from rank_rent.opportunity_review.services import (
    OpportunityReviewError,
    OpportunityReviewService,
)


@pytest.fixture
def session() -> Session:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as active:
        yield active


@pytest.fixture
def actor() -> ReviewActor:
    return ReviewActor(actor_id="reviewer-1", role=ReviewRole.reviewer)


def _opportunity(session: Session, suffix: str = "one") -> OpportunityORM:
    service = ServiceFamilyORM(
        slug=f"plumbing-{suffix}",
        display_name="Plumbing",
        seed_queries=["plumber"],
        provider_categories=["plumber"],
    )
    market = MarketORM(
        slug=f"st-louis-{suffix}",
        display_name="St. Louis, MO",
        state="MO",
        country_code="US",
        latitude=38.627,
        longitude=-90.1994,
        population=300_000,
        reference_population=335_000_000,
        geography_id=f"place:{suffix}",
        geography_dataset_version="fixture-v1",
        boundary_radius_km=25,
    )
    session.add_all([service, market])
    session.flush()
    opportunity = OpportunityORM(
        service_family_id=service.id,
        market_id=market.id,
        status=OpportunityState.discovered.value,
    )
    session.add(opportunity)
    session.flush()
    return opportunity


def _full_evidence(session: Session, opportunity: OpportunityORM) -> tuple[ScanRunORM, SerpResultORM]:
    now = datetime.now(UTC)
    scan = ScanRunORM(
        opportunity_id=opportunity.id,
        source="fixture",
        status="completed",
        data_mode="fixture",
        scan_profile="full",
        planned_cost_usd=0,
        actual_cost_usd=0,
        completed_at=now,
        request_parameters={
            "market_payload": {"display_name": "St. Louis, MO"},
            "public_data_prefilter": {"score": 72},
        },
        partial_outputs={"evidence_quality": {"status": "pass"}},
    )
    session.add(scan)
    session.flush()
    session.add(
        KeywordDecisionORM(
            scan_run_id=scan.id,
            keyword="plumber st louis",
            canonical_keyword="plumber st louis",
            decision="included",
            rank=1,
            representative=True,
        )
    )
    serp = SerpSnapshotORM(
        scan_run_id=scan.id,
        opportunity_id=opportunity.id,
        query="plumber st louis",
        market_id="place:one",
        device="desktop",
        captured_at=now,
    )
    session.add(serp)
    session.flush()
    result = SerpResultORM(
        serp_snapshot_id=serp.id,
        order=1,
        result_type="organic",
        url="https://example.test/plumbing",
        domain="example.test",
        title="St. Louis Plumbing",
        classification="local_provider",
    )
    session.add(result)
    session.add(
        CompetitorMetricORM(
            scan_run_id=scan.id,
            opportunity_id=opportunity.id,
            url="https://example.test/plumbing",
            domain="example.test",
            page_type="local_provider",
            captured_at=now,
        )
    )
    session.add(
        ProviderCandidateORM(
            scan_run_id=scan.id,
            opportunity_id=opportunity.id,
            name="Fixture Plumbing",
            categories=["plumber"],
            business_status="open",
            source="fixture",
            source_timestamp=now,
            outreach_status="not_contacted",
            suitability_score=78,
        )
    )
    score = FullOpportunityScoreORM(
        scan_run_id=scan.id,
        opportunity_id=opportunity.id,
        scoring_version="v2.12",
        total_score=74,
        confidence="medium",
        explanation="Fixture full score.",
        payload={"component_scores": {"demand": 12}},
    )
    session.add(score)
    session.add(
        ScoreComponentORM(
            scan_run_id=scan.id,
            component="demand",
            score=12,
            inputs={"volume": 100},
            formula="fixture",
        )
    )
    session.flush()
    return scan, result


def test_transitions_are_validated_versioned_and_attributable(
    session: Session,
    actor: ReviewActor,
) -> None:
    opportunity = _opportunity(session)
    service = OpportunityReviewService(session)
    service.assign_owner(
        opportunity.id,
        OwnershipRequest(owner_user_id="analyst-1", reason="Primary review owner."),
        actor,
    )

    with pytest.raises(OpportunityReviewError, match="invalid_opportunity_transition"):
        service.transition(
            opportunity.id,
            ReviewTransitionRequest(
                target_state=OpportunityState.approved_for_property,
                decision="approve",
                decision_reason="This skips required review states.",
            ),
            actor,
        )

    row = service.transition(
        opportunity.id,
        ReviewTransitionRequest(
            target_state=OpportunityState.prefilter_review,
            decision="prefilter_selected",
            decision_reason="Public data warrants testing review.",
            expected_review_version=1,
            tags=["Priority", "priority"],
        ),
        actor,
    )
    assert row.reviewer_user_id == "reviewer-1"
    assert row.owner_user_id == "analyst-1"
    assert row.tags == ["priority"]
    assert opportunity.review_version == 2
    assert session.query(OpportunityReviewORM).count() == 2

    with pytest.raises(OpportunityReviewError, match="version_conflict"):
        service.transition(
            opportunity.id,
            ReviewTransitionRequest(
                target_state=OpportunityState.testing_planned,
                decision="stale_request",
                decision_reason="A stale browser must not overwrite a newer review.",
                expected_review_version=1,
            ),
            actor,
        )


def test_approval_requires_complete_full_evidence_and_owner(
    session: Session,
    actor: ReviewActor,
) -> None:
    opportunity = _opportunity(session)
    review = OpportunityReviewService(session)
    review.transition_system(
        opportunity.id,
        OpportunityState.full_review,
        decision="fixture_full_review",
        reason="Fixture entered full review.",
    )

    with pytest.raises(OpportunityReviewError, match="approval_evidence_incomplete"):
        review.transition(
            opportunity.id,
            ReviewTransitionRequest(
                target_state=OpportunityState.approved_for_property,
                decision="approve",
                decision_reason="Attempt approval before evidence is complete.",
            ),
            actor,
        )

    review.assign_owner(
        opportunity.id,
        OwnershipRequest(owner_user_id="analyst-1", reason="Own underwriting review."),
        actor,
    )
    _full_evidence(session, opportunity)
    completeness = review.approval_completeness(opportunity.id)
    assert completeness.complete is True

    approved = review.transition(
        opportunity.id,
        ReviewTransitionRequest(
            target_state=OpportunityState.approved_for_property,
            decision="approve_property_investment",
            decision_reason="Full evidence packet passed every approval check.",
            notes="Proceed only to the separate property workflow.",
        ),
        actor,
    )
    assert opportunity.status == "approved_for_property"
    assert opportunity.approved_at is not None
    assert approved.approved_at is not None


def test_evidence_overrides_are_append_only_and_reversible(
    session: Session,
    actor: ReviewActor,
) -> None:
    opportunity = _opportunity(session)
    _, result = _full_evidence(session, opportunity)
    review = OpportunityReviewService(session)
    applied = review.apply_override(
        opportunity.id,
        EvidenceOverrideRequest(
            override_kind=EvidenceOverrideKind.serp_classification,
            target_record_id=result.id,
            field_name="classification",
            expected_original_value="local_provider",
            new_value="directory",
            reason="The page is a provider directory, not an operating plumber.",
            score_impact=-1.4,
            score_impact_explanation="A top-position directory reduces organic click availability.",
        ),
        actor,
    )
    assert result.classification == "local_provider"
    assert review.list_overrides(opportunity.id)[0]["active"] is True

    reversed_row = review.reverse_override(
        opportunity.id,
        applied.id,
        EvidenceOverrideReversalRequest(
            reason="A second review confirmed the result is the provider's own page.",
            score_impact=1.4,
            score_impact_explanation="Reversal restores the original scoring interpretation.",
        ),
        actor,
    )
    assert reversed_row.reverses_override_id == applied.id
    history = review.list_overrides(opportunity.id)
    assert history[0]["active"] is False
    assert history[1]["action"] == "revert"
    assert session.query(EvidenceOverrideORM).count() == 2


def test_saved_template_and_bounded_batch_testing_queue(
    session: Session,
    actor: ReviewActor,
) -> None:
    first = _opportunity(session, "one")
    second = _opportunity(session, "two")
    second.service_family_id = first.service_family_id
    session.flush()
    review = OpportunityReviewService(session)
    template = review.create_template(
        DiscoveryTemplateInput(
            name="Missouri plumbing test",
            service_family_id=first.service_family_id,
            market_filters={"states": ["MO"], "minimum_population": 25_000},
            prefilter_profile="home_services",
            budget_usd=Decimal("1.00"),
            freshness_requirements={"maximum_age_days": 30},
        ),
        actor,
    )
    batch = review.create_batch_plan(
        BatchPlanRequest(
            name="Two-market fixture batch",
            opportunity_ids=[first.id, second.id],
            data_mode="fixture",
            scan_profile="testing",
            aggregate_budget_usd=Decimal("1.00"),
            template_id=template.id,
        ),
        actor,
    )
    assert batch.aggregate_estimated_cost_usd == 0
    assert first.status == "testing_planned"
    assert second.status == "testing_planned"

    review.confirm_batch(
        batch.id,
        BatchConfirmationRequest(
            approved_max_cost_usd=Decimal("0.00"),
            reason="Fixture batch has a verified zero-dollar aggregate cost.",
        ),
        actor,
    )
    queued = review.queue_batch(batch.id, actor)
    assert len(queued.queued_scan_ids) == 2
    assert queued.aggregate_planned_cost_usd == Decimal("0")
    assert session.query(ScanRunORM).filter_by(source="batch_review_async").count() == 2


def test_batch_confirmation_cannot_exceed_budget(
    session: Session,
    actor: ReviewActor,
) -> None:
    batch = BatchScanPlanORM(
        name="Bounded batch",
        created_by=actor.actor_id,
        scan_profile="testing",
        data_mode="fixture",
        status="planned",
        aggregate_budget_usd=5,
        aggregate_estimated_cost_usd=3,
    )
    session.add(batch)
    session.flush()
    with pytest.raises(OpportunityReviewError, match="exceeds_plan_budget"):
        OpportunityReviewService(session).confirm_batch(
            batch.id,
            BatchConfirmationRequest(
                approved_max_cost_usd=Decimal("6"),
                reason="This approval exceeds the original batch budget.",
            ),
            actor,
        )


def test_property_operations_are_unavailable_before_approval(session: Session) -> None:
    opportunity = _opportunity(session)
    operations = ProviderOperationsService(session)
    with pytest.raises(
        ProviderAssignmentError,
        match="property_action_requires_approved_opportunity",
    ):
        operations.create_routing_profile(
            property_id="property-blocked",
            opportunity_id=opportunity.id,
        )


def test_evidence_packet_json_and_csv_api_are_complete(
    tmp_path,
    actor: ReviewActor,
) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'review-api.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup_session:
        opportunity = _opportunity(setup_session)
        _full_evidence(setup_session, opportunity)
        review = OpportunityReviewService(setup_session)
        review.assign_owner(
            opportunity.id,
            OwnershipRequest(owner_user_id="analyst-1", reason="Own evidence export."),
            actor,
        )
        setup_session.commit()
        opportunity_id = opportunity.id

    def override_session():
        with factory() as active:
            yield active

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        json_response = client.get(
            f"/api/opportunities/{opportunity_id}/evidence-packet",
            headers={"X-Actor-Id": "reviewer-1", "X-Actor-Role": "reviewer"},
        )
        assert json_response.status_code == 200
        packet = json_response.json()
        assert packet["market_evidence"]["public_data_prefilter"]["score"] == 72
        assert packet["keyword_decisions"]
        assert packet["serps"][0]["results"][0]["classification"] == "local_provider"
        assert packet["competitors"]
        assert packet["providers"]
        assert packet["score_trace"]["components"]
        assert "costs" in packet
        assert "freshness" in packet
        assert packet["review_notes"]

        csv_response = client.get(
            f"/api/opportunities/{opportunity_id}/evidence-packet",
            params={"format": "csv"},
            headers={"X-Actor-Id": "reviewer-1", "X-Actor-Role": "reviewer"},
        )
        assert csv_response.status_code == 200
        assert csv_response.headers["content-type"].startswith("text/csv")
        assert "keyword_decisions" in csv_response.text
        assert "review_notes" in csv_response.text
    finally:
        app.dependency_overrides.clear()
