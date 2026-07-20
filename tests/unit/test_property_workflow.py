from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from rank_rent.db.base import Base, get_session, make_engine
from rank_rent.db.orm import MarketORM, OpportunityORM, ServiceFamilyORM
from rank_rent.lead_routing.orm import (
    PropertyRoutingProfileORM,
    ProviderAssignmentORM,
)
from rank_rent.main import app
from rank_rent.opportunity_review.models import ReviewActor, ReviewRole
from rank_rent.property_workflow.adapters import FixtureDomainAvailabilityAdapter
from rank_rent.property_workflow.models import (
    BuildEnvironment,
    ComplianceReviewRequest,
    DeploymentRequest,
    DNSVerificationRequest,
    DomainAvailability,
    DomainDecisionRequest,
    DomainGenerationRequest,
    DomainPurchaseApprovalRequest,
    ManualRegistrationRequest,
    PropertyCreateRequest,
    ProviderActivationRequest,
    ProviderAssignmentInput,
    ProviderReplacementRequest,
    RollbackRequest,
    SiteBuildRequest,
    SiteConfigApprovalRequest,
    SiteConfigInput,
)
from rank_rent.property_workflow.orm import (
    DeploymentORM,
    PropertyORM,
    PropertyVersionORM,
    SiteBuildORM,
)
from rank_rent.property_workflow.services import (
    REQUIRED_COMPLIANCE_CHECKS,
    PropertyWorkflowError,
    PropertyWorkflowService,
)


@pytest.fixture
def session() -> Session:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as active:
        yield active


@pytest.fixture
def actors() -> dict[str, ReviewActor]:
    return {
        "operator": ReviewActor(actor_id="operator-1", role=ReviewRole.operator),
        "reviewer": ReviewActor(actor_id="reviewer-1", role=ReviewRole.reviewer),
        "admin": ReviewActor(actor_id="admin-1", role=ReviewRole.admin),
    }


def _opportunity(
    session: Session,
    *,
    approved: bool = True,
    suffix: str = "primary",
) -> OpportunityORM:
    service = ServiceFamilyORM(
        slug=f"water-heater-{suffix}",
        display_name="Water Heater Repair",
        seed_queries=["water heater repair"],
        provider_categories=["plumber"],
    )
    market = MarketORM(
        slug=f"st-louis-mo-property-{suffix}",
        display_name="St. Louis, MO",
        cities=["St. Louis"],
        state="MO",
        country_code="US",
    )
    session.add_all([service, market])
    session.flush()
    opportunity = OpportunityORM(
        service_family_id=service.id,
        market_id=market.id,
        status="approved_for_property" if approved else "full_review",
    )
    session.add(opportunity)
    session.flush()
    return opportunity


def _service(
    session: Session,
    tmp_path: Path,
) -> PropertyWorkflowService:
    return PropertyWorkflowService(
        session,
        build_root=tmp_path / "builds",
        deployment_root=tmp_path / "deployments",
    )


def _create_property(
    service: PropertyWorkflowService,
    opportunity: OpportunityORM,
    actor: ReviewActor,
) -> PropertyORM:
    return service.create_property(
        opportunity.id,
        PropertyCreateRequest(
            property_id="stl-water-help",
            neutral_brand="St. Louis Water Help",
            public_tracking_number="+13145550100",
            public_contact_email="help@example.test",
            analytics_config={"provider": "fixture", "verified": True},
        ),
        actor,
    )


def _config() -> SiteConfigInput:
    return SiteConfigInput(
        brand={"name": "St. Louis Water Help", "tagline": "Clear local guidance"},
        service={
            "name": "Water Heater Repair",
            "summary": "Useful repair and replacement request guidance.",
        },
        market={"name": "St. Louis, MO", "service_area": ["St. Louis"]},
        pricing_guidance={
            "text": "Final pricing depends on diagnosis and provider estimate.",
            "source": "operator-reviewed",
        },
        service_process=[
            {
                "title": "Describe the issue",
                "description": "Share the symptoms and timing without sensitive information.",
            },
            {
                "title": "Provider review",
                "description": "An independent provider reviews the request and provides terms.",
            },
        ],
        faqs=[
            {
                "question": "Does this website perform repairs?",
                "answer": "No. It is an independent referral and information property.",
            }
        ],
        local_considerations=[
            {
                "title": "Property access",
                "description": "Access and equipment location can affect an estimate.",
            }
        ],
        referral_disclosure=(
            "This independent referral website does not perform repair services. "
            "Requests may be shared with one reviewed independent provider."
        ),
        calls_to_action=[{"label": "Request provider review", "kind": "lead_form"}],
        asset_ids=[],
        metadata={
            "title": "St. Louis Water Heater Guidance",
            "description": "Independent water heater information and referral intake.",
        },
        analytics={"event_schema": "property-v1"},
        form_routing={"profile": "property-owned"},
        change_reason="Initial useful market-specific property content.",
    )


async def _verify_domain(
    service: PropertyWorkflowService,
    property_id: str,
    actor: ReviewActor,
) -> None:
    candidate = service.generate_domain_candidates(
        property_id,
        DomainGenerationRequest(limit=1),
    )[0]
    service.decide_domain_candidate(
        candidate.id,
        shortlist=True,
        request=DomainDecisionRequest(reason="Preferred neutral brand domain."),
        actor=actor,
    )
    await service.check_domain_availability(
        candidate.id,
        FixtureDomainAvailabilityAdapter(
            DomainAvailability.available,
            {"fixture_case": "available"},
        ),
    )
    registration = service.approve_domain_purchase(
        candidate.id,
        DomainPurchaseApprovalRequest(
            explicit_purchase_approval=True,
            reason="Operator explicitly approves manual purchase outside the application.",
        ),
        actor,
    )
    service.record_manual_registration(
        registration.id,
        ManualRegistrationRequest(
            external_reference="manual-receipt-001",
            registrar_name="fixture-registrar",
            expected_dns_records={"CNAME:www": "sites.example.test"},
        ),
        actor,
    )
    service.verify_dns(
        registration.id,
        DNSVerificationRequest(
            observed_records={"CNAME:www": "sites.example.test"},
            evidence_reference="fixture-dns-export-001",
        ),
        actor,
    )


class _UnsafeRegistrar:
    can_purchase = True


class _UnsafePublicDeployment:
    public_capable = True

    async def deploy(self, source: Path, destination: Path):
        raise AssertionError("The unsafe adapter must be rejected before deployment.")


def _active_provider(
    service: PropertyWorkflowService,
    property_id: str,
    actors: dict[str, ReviewActor],
    name: str = "Reviewed Plumbing Co",
) -> ProviderAssignmentORM:
    assignment = service.create_provider_assignment(
        property_id,
        ProviderAssignmentInput(
            public_business_name=name,
            destination_phone="+13145550101",
            service_radius={"radius_miles": 25},
            credentials=[{"name": "Insurance", "source": "operator document review"}],
            license_numbers=[{"value": "MO-FIXTURE", "source": "fixture registry"}],
            approved_claims=[{"claim": "Water heater service", "source": "provider agreement"}],
            claims_review_reason="Reviewer checked each provider-specific field and source.",
        ),
        actors["reviewer"],
    )
    return service.activate_provider(
        assignment.id,
        ProviderActivationRequest(reason="Operator activates reviewed exclusive provider."),
        actors["operator"],
    )


def test_property_requires_approved_opportunity_and_creates_routing_compatibility(
    session: Session,
    actors: dict[str, ReviewActor],
    tmp_path: Path,
) -> None:
    service = _service(session, tmp_path)
    blocked = _opportunity(session, approved=False, suffix="blocked")
    with pytest.raises(
        PropertyWorkflowError,
        match="property_action_requires_approved_opportunity",
    ):
        _create_property(service, blocked, actors["operator"])

    approved = _opportunity(session, suffix="approved")
    prop = _create_property(service, approved, actors["operator"])
    profile = session.scalar(
        select(PropertyRoutingProfileORM).where(
            PropertyRoutingProfileORM.property_id == prop.id
        )
    )
    version = session.scalar(
        select(PropertyVersionORM).where(PropertyVersionORM.property_id == prop.id)
    )
    assert profile is not None
    assert profile.opportunity_id == approved.id
    assert version is not None
    assert version.snapshot["neutral_brand"] == "St. Louis Water Help"
    assert len(version.snapshot_sha256) == 64


@pytest.mark.asyncio
async def test_domain_flow_requires_explicit_approval_and_matching_dns(
    session: Session,
    actors: dict[str, ReviewActor],
    tmp_path: Path,
) -> None:
    service = _service(session, tmp_path)
    prop = _create_property(service, _opportunity(session), actors["operator"])
    candidate = service.generate_domain_candidates(
        prop.id,
        DomainGenerationRequest(limit=1),
    )[0]
    service.decide_domain_candidate(
        candidate.id,
        shortlist=True,
        request=DomainDecisionRequest(reason="Shortlisted for neutral branding."),
        actor=actors["operator"],
    )
    await service.check_domain_availability(
        candidate.id,
        FixtureDomainAvailabilityAdapter(DomainAvailability.available),
    )
    with pytest.raises(PropertyWorkflowError, match="automatic_domain_purchase_is_disabled"):
        service.approve_domain_purchase(
            candidate.id,
            DomainPurchaseApprovalRequest(
                explicit_purchase_approval=True,
                reason="Even explicit approval cannot enable an automatic purchase adapter.",
            ),
            actors["operator"],
            registrar=_UnsafeRegistrar(),
        )
    with pytest.raises(
        PropertyWorkflowError,
        match="explicit_domain_purchase_approval_required",
    ):
        service.approve_domain_purchase(
            candidate.id,
            DomainPurchaseApprovalRequest(
                explicit_purchase_approval=False,
                reason="This intentionally verifies the fail-closed gate.",
            ),
            actors["operator"],
        )
    registration = service.approve_domain_purchase(
        candidate.id,
        DomainPurchaseApprovalRequest(
            explicit_purchase_approval=True,
            reason="Manual registration has explicit operator approval.",
        ),
        actors["operator"],
    )
    service.record_manual_registration(
        registration.id,
        ManualRegistrationRequest(
            external_reference="receipt-1",
            expected_dns_records={"A:@": "192.0.2.10"},
        ),
        actors["operator"],
    )
    with pytest.raises(PropertyWorkflowError, match="dns_records_do_not_match"):
        service.verify_dns(
            registration.id,
            DNSVerificationRequest(
                observed_records={"A:@": "192.0.2.11"},
                evidence_reference="dns-export-wrong",
            ),
            actors["operator"],
        )
    service.verify_dns(
        registration.id,
        DNSVerificationRequest(
            observed_records={"A:@": "192.0.2.10"},
            evidence_reference="dns-export-correct",
        ),
        actors["operator"],
    )
    assert prop.domain == candidate.domain


def test_provider_replacement_is_configuration_only_and_exclusive(
    session: Session,
    actors: dict[str, ReviewActor],
    tmp_path: Path,
) -> None:
    service = _service(session, tmp_path)
    prop = _create_property(service, _opportunity(session), actors["operator"])
    current = _active_provider(service, prop.id, actors)
    config = service.create_site_config(prop.id, _config(), actors["operator"])
    original_hash = config.config_sha256
    original_property_version = prop.current_version
    replacement = service.create_provider_assignment(
        prop.id,
        ProviderAssignmentInput(
            public_business_name="Replacement Plumbing Co",
            destination_email="dispatch@example.test",
            approved_claims=[{"claim": "Repair intake", "source": "signed fixture agreement"}],
            claims_review_reason="Reviewer validated replacement claims and routing details.",
        ),
        actors["reviewer"],
    )
    old, active = service.replace_provider(
        prop.id,
        ProviderReplacementRequest(
            replacement_assignment_id=replacement.id,
            reason="Provider replacement approved without changing the property asset.",
        ),
        actors["operator"],
    )
    assert old.id == current.id
    assert old.status == "replaced"
    assert active.status == "active"
    assert active.activation_approved_by == actors["operator"].actor_id
    assert active.replacement_approved_by == actors["operator"].actor_id
    assert config.config_sha256 == original_hash
    assert prop.current_version == original_property_version
    active_rows = session.scalars(
        select(ProviderAssignmentORM).where(
            ProviderAssignmentORM.property_id == prop.id,
            ProviderAssignmentORM.status == "active",
        )
    ).all()
    assert [row.id for row in active_rows] == [active.id]


@pytest.mark.asyncio
async def test_builds_are_reproducible_accessible_disclosed_and_staging_noindex(
    session: Session,
    actors: dict[str, ReviewActor],
    tmp_path: Path,
) -> None:
    service = _service(session, tmp_path)
    prop = _create_property(service, _opportunity(session), actors["operator"])
    config = service.create_site_config(prop.id, _config(), actors["operator"])
    first = service.build_site(
        config.id,
        SiteBuildRequest(environment=BuildEnvironment.staging),
    )
    second = service.build_site(
        config.id,
        SiteBuildRequest(environment=BuildEnvironment.staging),
    )
    assert second.id == first.id
    assert first.validation_report["passed"] is True
    assert all(first.validation_report["checks"].values())
    assert len(first.build_sha256) == 64
    output = Path(first.output_path)
    assert "Disallow: /" in (output / "robots.txt").read_text()
    index = (output / "index.html").read_text()
    assert "noindex,nofollow" in index
    assert "independent referral" in index.lower()
    assert '"@type":"WebSite"' in index
    assert "LocalBusiness" not in index
    assert (output / "sitemap.xml").read_text().startswith("<?xml")


@pytest.mark.asyncio
async def test_production_deployment_gates_and_rollback(
    session: Session,
    actors: dict[str, ReviewActor],
    tmp_path: Path,
) -> None:
    service = _service(session, tmp_path)
    prop = _create_property(service, _opportunity(session), actors["operator"])
    config = service.create_site_config(prop.id, _config(), actors["operator"])
    service.review_site_config(
        config.id,
        SiteConfigApprovalRequest(
            approved=True,
            reason="Reviewer approves useful, sourced, disclosed content.",
        ),
        actors["reviewer"],
    )
    with pytest.raises(
        PropertyWorkflowError,
        match="production_build_requires_verified_domain",
    ):
        service.build_site(
            config.id,
            SiteBuildRequest(environment=BuildEnvironment.production),
        )
    await _verify_domain(service, prop.id, actors["operator"])
    first_build = service.build_site(
        config.id,
        SiteBuildRequest(environment=BuildEnvironment.production),
    )
    with pytest.raises(
        PropertyWorkflowError,
        match="public_deployment_adapter_not_approved",
    ):
        await service.deploy(
            first_build.id,
            DeploymentRequest(
                environment=BuildEnvironment.production,
                operator_confirmation=True,
                confirmation_reason="Unsafe adapter rejection test.",
            ),
            actors["operator"],
            adapter=_UnsafePublicDeployment(),
        )
    with pytest.raises(
        PropertyWorkflowError,
        match="production_deployment_gates_failed",
    ) as failure:
        await service.deploy(
            first_build.id,
            DeploymentRequest(
                environment=BuildEnvironment.production,
                operator_confirmation=True,
                confirmation_reason="Attempt before all operational gates.",
            ),
            actors["operator"],
        )
    assert "active_provider_or_neutral_pilot" in failure.value.detail["failed_gates"]
    assert "compliance_approved" in failure.value.detail["failed_gates"]
    assert "routing_healthy" in failure.value.detail["failed_gates"]

    _active_provider(service, prop.id, actors)
    profile = session.scalar(
        select(PropertyRoutingProfileORM).where(
            PropertyRoutingProfileORM.property_id == prop.id
        )
    )
    assert profile is not None
    profile.routing_health_status = "healthy"
    compliance = service.review_compliance(
        first_build.id,
        ComplianceReviewRequest(
            approved=True,
            checklist={key: True for key in REQUIRED_COMPLIANCE_CHECKS},
            notes="Reviewer verified the complete production compliance checklist.",
        ),
        actors["reviewer"],
    )
    first_deployment = await service.deploy(
        first_build.id,
        DeploymentRequest(
            environment=BuildEnvironment.production,
            operator_confirmation=True,
            confirmation_reason="Operator confirms the complete local production release.",
        ),
        actors["operator"],
    )
    assert first_deployment.local_only is True
    assert first_deployment.compliance_review_id == compliance.id
    assert first_deployment.gate_snapshot["domain_and_dns_verified"] is True
    assert Path(first_deployment.url.removeprefix("file://")).name == "index.html"
    deployed_root = Path(first_deployment.url.removeprefix("file://")).parent
    provider_runtime = json.loads(
        (deployed_root / "provider-config.json").read_text()
    )
    assert provider_runtime["public_business_name"] == "Reviewed Plumbing Co"

    replacement = service.create_provider_assignment(
        prop.id,
        ProviderAssignmentInput(
            public_business_name="Runtime Replacement Co",
            destination_email="replacement@example.test",
            approved_claims=[{"claim": "Repair intake", "source": "reviewed agreement"}],
            claims_review_reason="Reviewer validates runtime replacement configuration.",
        ),
        actors["reviewer"],
    )
    service.replace_provider(
        prop.id,
        ProviderReplacementRequest(
            replacement_assignment_id=replacement.id,
            reason="Operator replaces the provider without rebuilding the SEO property.",
        ),
        actors["operator"],
    )
    updated_runtime = json.loads(
        (deployed_root / "provider-config.json").read_text()
    )
    assert updated_runtime["public_business_name"] == "Runtime Replacement Co"
    assert first_build.build_sha256 == session.get(SiteBuildORM, first_build.id).build_sha256

    updated = _config().model_copy(
        update={
            "metadata": {
                "title": "St. Louis Water Heater Request Guide",
                "description": "Updated independent referral and request guidance.",
            },
            "change_reason": "Reviewed metadata refinement.",
        }
    )
    second_config = service.create_site_config(prop.id, updated, actors["operator"])
    service.review_site_config(
        second_config.id,
        SiteConfigApprovalRequest(
            approved=True,
            reason="Reviewer approves the second deterministic configuration.",
        ),
        actors["reviewer"],
    )
    second_build = service.build_site(
        second_config.id,
        SiteBuildRequest(environment=BuildEnvironment.production),
    )
    service.review_compliance(
        second_build.id,
        ComplianceReviewRequest(
            approved=True,
            checklist={key: True for key in REQUIRED_COMPLIANCE_CHECKS},
            notes="Reviewer approves the second build compliance evidence.",
        ),
        actors["reviewer"],
    )
    second_deployment = await service.deploy(
        second_build.id,
        DeploymentRequest(
            environment=BuildEnvironment.production,
            operator_confirmation=True,
            confirmation_reason="Operator confirms the second local production release.",
        ),
        actors["operator"],
    )
    assert first_deployment.status == "rolled_back"
    assert second_deployment.previous_deployment_id == first_deployment.id

    rollback = await service.rollback(
        prop.id,
        RollbackRequest(
            target_deployment_id=first_deployment.id,
            operator_confirmation=True,
            reason="Restore the prior reviewed production build after release validation.",
        ),
        actors["operator"],
    )
    assert rollback.site_build_id == first_build.id
    assert rollback.rollback_of_deployment_id == second_deployment.id
    rollback_root = Path(rollback.url.removeprefix("file://")).parent
    rollback_provider = json.loads(
        (rollback_root / "provider-config.json").read_text()
    )
    assert rollback_provider["public_business_name"] == "Runtime Replacement Co"
    active_deployments = session.scalars(
        select(DeploymentORM).where(
            DeploymentORM.property_id == prop.id,
            DeploymentORM.environment == "production",
            DeploymentORM.status == "deployed",
        )
    ).all()
    assert [row.id for row in active_deployments] == [rollback.id]


def test_site_config_rejects_fake_identity_and_unreviewed_provider_details() -> None:
    with pytest.raises(ValueError, match="unsupported claim"):
        _config().model_copy(
            update={"metadata": {"title": "Test", "description": "Guaranteed ranking"}}
        ).model_validate(
            _config().model_copy(
                update={"metadata": {"title": "Test", "description": "Guaranteed ranking"}}
            ).model_dump()
        )


def test_property_api_initializes_and_lists_operator_workflow(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'property-api.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        opportunity = _opportunity(setup)
        setup.commit()
        opportunity_id = opportunity.id

    def override_session():
        with factory() as active:
            yield active

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/opportunities/{opportunity_id}/property",
            headers={"X-Actor-Id": "operator-api", "X-Actor-Role": "operator"},
            json={
                "property_id": "api-property",
                "neutral_brand": "API Water Guide",
                "analytics_config": {"verified": False},
            },
        )
        assert response.status_code == 200
        assert response.json()["property"]["id"] == "api-property"

        generated = client.post(
            "/api/properties/api-property/domain-candidates/generate",
            json={"limit": 3, "tlds": ["com"]},
        )
        assert generated.status_code == 200
        assert len(generated.json()["domain_candidates"]) == 3

        listing = client.get("/api/properties")
        assert listing.status_code == 200
        assert listing.json()["properties"][0]["opportunity_id"] == opportunity_id
    finally:
        app.dependency_overrides.clear()
