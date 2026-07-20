from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rank_rent.db.orm import OpportunityORM, ProviderCandidateORM
from rank_rent.lead_routing.models import ProviderAssignmentStatus
from rank_rent.lead_routing.orm import (
    PropertyRoutingProfileORM,
    ProviderAssignmentORM,
)
from rank_rent.lead_routing.services import (
    ProviderAssignmentError,
    ProviderOperationsService,
)
from rank_rent.opportunity_review.models import ReviewActor, ReviewRole
from rank_rent.opportunity_review.services import (
    OpportunityReviewError,
    require_property_approval,
)
from rank_rent.property_workflow.adapters import (
    DeploymentAdapter,
    DomainAvailabilityAdapter,
    LocalFilesystemDeploymentAdapter,
    ManualRegistrarAdapter,
    RegistrarAdapter,
)
from rank_rent.property_workflow.builder import (
    BUILDER_VERSION,
    build_static_site,
)
from rank_rent.property_workflow.models import (
    AssetApprovalRequest,
    AssetCreateRequest,
    BuildEnvironment,
    ComplianceReviewRequest,
    ComplianceStatus,
    DeploymentRequest,
    DeploymentStatus,
    DNSVerificationRequest,
    DomainAvailability,
    DomainCandidateStatus,
    DomainDecisionRequest,
    DomainGenerationRequest,
    DomainPurchaseApprovalRequest,
    DomainRegistrationStatus,
    ManualRegistrationRequest,
    PropertyCreateRequest,
    PropertyStatus,
    PropertyUpdateRequest,
    ProviderActivationRequest,
    ProviderAssignmentInput,
    ProviderReplacementRequest,
    RollbackRequest,
    SiteBuildRequest,
    SiteConfigApprovalRequest,
    SiteConfigInput,
    SiteConfigStatus,
)
from rank_rent.property_workflow.orm import (
    AssetORM,
    ComplianceReviewORM,
    DeploymentORM,
    DomainCandidateORM,
    DomainRegistrationORM,
    PropertyORM,
    PropertyVersionORM,
    SiteBuildORM,
    SiteConfigORM,
)
from rank_rent.settings import get_settings

REQUIRED_COMPLIANCE_CHECKS = {
    "accessibility",
    "content_accuracy",
    "privacy",
    "provider_claims",
    "referral_disclosure",
    "structured_data",
}


class PropertyWorkflowError(RuntimeError):
    def __init__(self, code: str, detail: Any | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _now() -> datetime:
    return datetime.now(UTC)


def _json_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _human(actor: ReviewActor) -> None:
    if actor.role == ReviewRole.system:
        raise PropertyWorkflowError("human_operator_required")


def _reviewer(actor: ReviewActor) -> None:
    if actor.role not in {ReviewRole.reviewer, ReviewRole.admin}:
        raise PropertyWorkflowError("reviewer_role_required")


def _operator(actor: ReviewActor) -> None:
    if actor.role not in {ReviewRole.operator, ReviewRole.admin}:
        raise PropertyWorkflowError("operator_role_required")


class PropertyWorkflowService:
    def __init__(
        self,
        session: Session,
        *,
        build_root: Path | None = None,
        deployment_root: Path | None = None,
    ) -> None:
        self.session = session
        runtime_root = get_settings().project_root / ".runtime" / "properties"
        self.build_root = build_root or runtime_root / "builds"
        self.deployment_root = deployment_root or runtime_root / "deployments"
        self.provider_operations = ProviderOperationsService(session)

    def create_property(
        self,
        opportunity_id: int,
        request: PropertyCreateRequest,
        actor: ReviewActor,
    ) -> PropertyORM:
        _human(actor)
        try:
            opportunity = require_property_approval(self.session, opportunity_id)
        except OpportunityReviewError as exc:
            raise PropertyWorkflowError(exc.code, exc.detail) from exc
        existing = self.session.scalar(
            select(PropertyORM).where(PropertyORM.opportunity_id == opportunity_id)
        )
        if existing is not None:
            raise PropertyWorkflowError(
                "opportunity_already_has_property", {"property_id": existing.id}
            )
        property_id = request.property_id or (
            f"property-{opportunity_id}-{_slug(request.neutral_brand)[:70]}"
        )
        row = PropertyORM(
            id=property_id,
            opportunity_id=opportunity.id,
            neutral_brand=request.neutral_brand,
            service_family_id=opportunity.service_family_id,
            market_id=opportunity.market_id,
            public_tracking_number=request.public_tracking_number,
            public_contact_email=request.public_contact_email,
            status=PropertyStatus.draft.value,
            analytics_config=request.analytics_config,
            current_version=1,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise PropertyWorkflowError("property_identifier_conflict") from exc
        self.provider_operations.create_routing_profile(
            property_id=row.id,
            opportunity_id=opportunity.id,
            public_tracking_number=row.public_tracking_number,
            public_contact_email=row.public_contact_email,
        )
        self._snapshot(row, actor, "Initial approved-opportunity property configuration.")
        return row

    def update_property(
        self,
        property_id: str,
        request: PropertyUpdateRequest,
        actor: ReviewActor,
    ) -> PropertyORM:
        _human(actor)
        row = self._property(property_id)
        fields = request.model_fields_set
        if "neutral_brand" in fields and request.neutral_brand is not None:
            row.neutral_brand = PropertyCreateRequest(
                neutral_brand=request.neutral_brand
            ).neutral_brand
        if "public_tracking_number" in fields:
            row.public_tracking_number = request.public_tracking_number
        if "public_contact_email" in fields:
            validated = PropertyCreateRequest(
                neutral_brand=row.neutral_brand,
                public_contact_email=request.public_contact_email,
            )
            row.public_contact_email = validated.public_contact_email
        if "analytics_config" in fields and request.analytics_config is not None:
            row.analytics_config = request.analytics_config
        profile = self._routing_profile(property_id)
        profile.public_tracking_number = row.public_tracking_number
        profile.public_contact_email = row.public_contact_email
        row.current_version += 1
        self._snapshot(row, actor, request.reason)
        self.session.flush()
        return row

    def generate_domain_candidates(
        self,
        property_id: str,
        request: DomainGenerationRequest,
    ) -> list[DomainCandidateORM]:
        row = self._property(property_id)
        opportunity = self.session.get(OpportunityORM, row.opportunity_id)
        assert opportunity is not None
        market = opportunity.market
        service = opportunity.service_family
        city = (market.cities or [market.display_name.split(",")[0]])[0]
        brand = _slug(row.neutral_brand)
        service_slug = _slug(service.display_name)
        city_slug = _slug(city)
        stems = [
            brand,
            f"{city_slug}-{service_slug}",
            f"{service_slug}-{city_slug}",
            f"{city_slug}-{service_slug}-guide",
            f"{service_slug}-help-{city_slug}",
            f"{city_slug}-service-connect",
        ]
        domains = [
            f"{stem}.{tld}"
            for stem in stems
            for tld in request.tlds
        ][: request.limit]
        existing = {
            item.domain: item
            for item in self.session.scalars(
                select(DomainCandidateORM).where(
                    DomainCandidateORM.property_id == property_id
                )
            )
        }
        result: list[DomainCandidateORM] = []
        for domain in domains:
            candidate = existing.get(domain)
            if candidate is None:
                candidate = DomainCandidateORM(property_id=property_id, domain=domain)
                self.session.add(candidate)
            result.append(candidate)
        self.session.flush()
        return result

    def decide_domain_candidate(
        self,
        candidate_id: int,
        *,
        shortlist: bool,
        request: DomainDecisionRequest,
        actor: ReviewActor,
    ) -> DomainCandidateORM:
        _human(actor)
        candidate = self._candidate(candidate_id)
        candidate.status = (
            DomainCandidateStatus.shortlisted.value
            if shortlist
            else DomainCandidateStatus.rejected.value
        )
        candidate.decision_by = actor.actor_id
        candidate.decision_reason = request.reason
        candidate.decided_at = _now()
        self.session.flush()
        return candidate

    async def check_domain_availability(
        self,
        candidate_id: int,
        adapter: DomainAvailabilityAdapter,
    ) -> DomainCandidateORM:
        candidate = self._candidate(candidate_id)
        evidence = await adapter.check(candidate.domain)
        candidate.availability_status = evidence.status.value
        candidate.availability_checked_at = _now()
        candidate.availability_evidence = {
            "provider": evidence.provider,
            **evidence.evidence,
        }
        self.session.flush()
        return candidate

    def approve_domain_purchase(
        self,
        candidate_id: int,
        request: DomainPurchaseApprovalRequest,
        actor: ReviewActor,
        registrar: RegistrarAdapter | None = None,
    ) -> DomainRegistrationORM:
        _operator(actor)
        candidate = self._candidate(candidate_id)
        if candidate.status != DomainCandidateStatus.shortlisted.value:
            raise PropertyWorkflowError("domain_must_be_shortlisted")
        if candidate.availability_status != DomainAvailability.available.value:
            raise PropertyWorkflowError("domain_must_have_available_evidence")
        if not request.explicit_purchase_approval:
            raise PropertyWorkflowError("explicit_domain_purchase_approval_required")
        selected = registrar or ManualRegistrarAdapter()
        if selected.can_purchase:
            raise PropertyWorkflowError("automatic_domain_purchase_is_disabled")
        existing = self.session.scalar(
            select(DomainRegistrationORM).where(
                DomainRegistrationORM.domain_candidate_id == candidate.id
            )
        )
        if existing is not None:
            return existing
        now = _now()
        registration = DomainRegistrationORM(
            property_id=candidate.property_id,
            domain_candidate_id=candidate.id,
            domain=candidate.domain,
            registrar_name="manual",
            status=DomainRegistrationStatus.purchase_approved.value,
            purchase_approved=True,
            purchase_approved_by=actor.actor_id,
            purchase_approval_reason=request.reason,
            purchase_approved_at=now,
        )
        self.session.add(registration)
        self.session.flush()
        return registration

    def record_manual_registration(
        self,
        registration_id: int,
        request: ManualRegistrationRequest,
        actor: ReviewActor,
    ) -> DomainRegistrationORM:
        _operator(actor)
        row = self._registration(registration_id)
        if not row.purchase_approved:
            raise PropertyWorkflowError("domain_purchase_was_not_approved")
        row.external_reference = request.external_reference
        row.registrar_name = request.registrar_name
        row.expected_dns_records = request.expected_dns_records
        row.registered_at = _now()
        row.status = DomainRegistrationStatus.manually_registered.value
        self.session.flush()
        return row

    def verify_dns(
        self,
        registration_id: int,
        request: DNSVerificationRequest,
        actor: ReviewActor,
    ) -> DomainRegistrationORM:
        _operator(actor)
        row = self._registration(registration_id)
        if row.status != DomainRegistrationStatus.manually_registered.value:
            raise PropertyWorkflowError("dns_verification_requires_manual_registration")
        if not row.expected_dns_records:
            raise PropertyWorkflowError("expected_dns_records_required")
        mismatch = {
            key: value
            for key, value in row.expected_dns_records.items()
            if request.observed_records.get(key) != value
        }
        if mismatch:
            raise PropertyWorkflowError("dns_records_do_not_match", {"mismatch": mismatch})
        row.observed_dns_records = request.observed_records
        row.dns_evidence_reference = request.evidence_reference
        row.dns_verified_at = _now()
        row.dns_verified_by = actor.actor_id
        row.status = DomainRegistrationStatus.dns_verified.value
        prop = self._property(row.property_id)
        prop.domain = row.domain
        prop.current_version += 1
        self._snapshot(prop, actor, "Verified manually registered domain and DNS records.")
        self.session.flush()
        return row

    def create_asset(
        self,
        property_id: str,
        request: AssetCreateRequest,
    ) -> AssetORM:
        self._property(property_id)
        row = AssetORM(
            property_id=property_id,
            **request.model_dump(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def review_asset(
        self,
        asset_id: int,
        request: AssetApprovalRequest,
        actor: ReviewActor,
    ) -> AssetORM:
        _reviewer(actor)
        row = self.session.get(AssetORM, asset_id)
        if row is None:
            raise PropertyWorkflowError("asset_not_found")
        row.approved = request.approved
        row.approved_by = actor.actor_id
        row.approval_reason = request.reason
        row.approved_at = _now()
        self.session.flush()
        return row

    def create_provider_assignment(
        self,
        property_id: str,
        request: ProviderAssignmentInput,
        actor: ReviewActor,
    ) -> ProviderAssignmentORM:
        _reviewer(actor)
        self._property(property_id)
        if request.provider_candidate_id is not None:
            candidate = self.session.get(ProviderCandidateORM, request.provider_candidate_id)
            if candidate is None:
                raise PropertyWorkflowError("provider_candidate_not_found")
        for asset_id in [
            *request.provider_photos,
            *([request.logo_asset_id] if request.logo_asset_id is not None else []),
        ]:
            asset = self.session.get(AssetORM, asset_id)
            if asset is None or asset.property_id != property_id or not asset.approved:
                raise PropertyWorkflowError(
                    "provider_asset_must_be_approved", {"asset_id": asset_id}
                )
        try:
            row = self.provider_operations.create_assignment(
                property_id=property_id,
                public_business_name=request.public_business_name,
                provider_candidate_id=request.provider_candidate_id,
                destination_phone=request.destination_phone,
                destination_email=request.destination_email,
                coverage=request.service_radius,
            )
        except ProviderAssignmentError as exc:
            raise PropertyWorkflowError(str(exc)) from exc
        row.logo_asset_id = request.logo_asset_id
        row.hours = request.hours
        row.service_radius = request.service_radius
        row.credentials = request.credentials
        row.license_numbers = request.license_numbers
        row.approved_claims = request.approved_claims
        row.attributed_testimonials = request.attributed_testimonials
        row.provider_photos = request.provider_photos
        row.claims_reviewed_by = actor.actor_id
        row.claims_reviewed_at = _now()
        row.claims_review_reason = request.claims_review_reason
        self.session.flush()
        return row

    def activate_provider(
        self,
        assignment_id: int,
        request: ProviderActivationRequest,
        actor: ReviewActor,
    ) -> ProviderAssignmentORM:
        _operator(actor)
        row = self._assignment(assignment_id)
        if row.claims_reviewed_at is None:
            raise PropertyWorkflowError("provider_claims_require_review")
        try:
            if row.status == ProviderAssignmentStatus.candidate.value:
                self.provider_operations.transition(
                    row.id, ProviderAssignmentStatus.pilot
                )
            active = self.provider_operations.transition(
                row.id, ProviderAssignmentStatus.active
            )
            active.activation_approved_by = actor.actor_id
            active.activation_reason = request.reason
            self._update_provider_runtime_configuration(active.property_id, active)
            self.session.flush()
            return active
        except ProviderAssignmentError as exc:
            raise PropertyWorkflowError(str(exc)) from exc

    def replace_provider(
        self,
        property_id: str,
        request: ProviderReplacementRequest,
        actor: ReviewActor,
    ) -> tuple[ProviderAssignmentORM, ProviderAssignmentORM]:
        _operator(actor)
        current = self.session.scalar(
            select(ProviderAssignmentORM).where(
                ProviderAssignmentORM.property_id == property_id,
                ProviderAssignmentORM.status == ProviderAssignmentStatus.active.value,
            )
        )
        if current is None:
            raise PropertyWorkflowError("active_provider_assignment_not_found")
        replacement = self._assignment(request.replacement_assignment_id)
        if replacement.claims_reviewed_at is None:
            raise PropertyWorkflowError("replacement_claims_require_review")
        try:
            prior, active = self.provider_operations.replace_assignment(
                current.id,
                replacement.id,
                reason=request.reason,
            )
            active.activation_approved_by = actor.actor_id
            active.activation_reason = request.reason
            active.replacement_approved_by = actor.actor_id
            self._update_provider_runtime_configuration(property_id, active)
            self.session.flush()
            return prior, active
        except ProviderAssignmentError as exc:
            raise PropertyWorkflowError(str(exc)) from exc

    def create_site_config(
        self,
        property_id: str,
        request: SiteConfigInput,
        actor: ReviewActor,
    ) -> SiteConfigORM:
        _human(actor)
        prop = self._property(property_id)
        payload = request.model_dump(exclude={"change_reason"}, mode="json")
        self._validate_assets(property_id, request.asset_ids)
        self._validate_provider_content(property_id, request.provider_details)
        latest_version = self.session.scalar(
            select(SiteConfigORM.version)
            .where(SiteConfigORM.property_id == property_id)
            .order_by(SiteConfigORM.version.desc())
            .limit(1)
        )
        row = SiteConfigORM(
            property_id=property_id,
            version=(latest_version or 0) + 1,
            status=SiteConfigStatus.draft.value,
            config_payload=payload,
            config_sha256=_json_hash(payload),
            created_by=actor.actor_id,
            change_reason=request.change_reason,
        )
        self.session.add(row)
        prop.current_version += 1
        self._snapshot(prop, actor, f"Created SiteConfig version {row.version}.")
        self.session.flush()
        return row

    def review_site_config(
        self,
        config_id: int,
        request: SiteConfigApprovalRequest,
        actor: ReviewActor,
    ) -> SiteConfigORM:
        _reviewer(actor)
        row = self._config(config_id)
        now = _now()
        if request.approved:
            for prior in self.session.scalars(
                select(SiteConfigORM).where(
                    SiteConfigORM.property_id == row.property_id,
                    SiteConfigORM.status == SiteConfigStatus.approved.value,
                    SiteConfigORM.id != row.id,
                )
            ):
                prior.status = SiteConfigStatus.superseded.value
            row.status = SiteConfigStatus.approved.value
            prop = self._property(row.property_id)
            prop.active_site_config_version = row.version
        else:
            row.status = SiteConfigStatus.draft.value
        row.approved_by = actor.actor_id
        row.approval_reason = request.reason
        row.approved_at = now
        self.session.flush()
        return row

    def build_site(
        self,
        config_id: int,
        request: SiteBuildRequest,
    ) -> SiteBuildORM:
        config = self._config(config_id)
        prop = self._property(config.property_id)
        if request.environment == BuildEnvironment.production:
            if config.status != SiteConfigStatus.approved.value:
                raise PropertyWorkflowError("production_build_requires_approved_config")
            if prop.domain is None:
                raise PropertyWorkflowError("production_build_requires_verified_domain")
        build_payload = {
            **config.config_payload,
            "asset_provenance": self._asset_provenance(
                config.property_id,
                list(config.config_payload.get("asset_ids", [])),
            ),
        }
        artifact = build_static_site(
            build_payload,
            domain=prop.domain,
            environment=request.environment,
            output_root=self.build_root / prop.id / request.environment.value,
        )
        existing = self.session.scalar(
            select(SiteBuildORM).where(
                SiteBuildORM.site_config_id == config.id,
                SiteBuildORM.environment == request.environment.value,
                SiteBuildORM.build_sha256 == artifact.checksum,
            )
        )
        if existing is not None:
            return existing
        row = SiteBuildORM(
            property_id=prop.id,
            site_config_id=config.id,
            environment=request.environment.value,
            builder_version=BUILDER_VERSION,
            build_sha256=artifact.checksum,
            output_path=str(artifact.output_path),
            status="passed" if artifact.validation["passed"] else "failed",
            file_count=len(artifact.manifest),
            total_bytes=artifact.total_bytes,
            validation_report=artifact.validation,
            manifest=artifact.manifest,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def review_compliance(
        self,
        build_id: int,
        request: ComplianceReviewRequest,
        actor: ReviewActor,
    ) -> ComplianceReviewORM:
        _reviewer(actor)
        build = self._build(build_id)
        missing = sorted(REQUIRED_COMPLIANCE_CHECKS - set(request.checklist))
        failed = sorted(key for key in REQUIRED_COMPLIANCE_CHECKS if not request.checklist.get(key))
        if request.approved and (missing or failed):
            raise PropertyWorkflowError(
                "compliance_checklist_incomplete",
                {"missing": missing, "failed": failed},
            )
        if request.approved and not build.validation_report.get("passed"):
            raise PropertyWorkflowError("failed_build_cannot_pass_compliance")
        row = ComplianceReviewORM(
            property_id=build.property_id,
            site_config_id=build.site_config_id,
            site_build_id=build.id,
            status=(
                ComplianceStatus.approved.value
                if request.approved
                else ComplianceStatus.rejected.value
            ),
            checklist=request.checklist,
            validation_snapshot=build.validation_report,
            reviewer_user_id=actor.actor_id,
            notes=request.notes,
            reviewed_at=_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    async def deploy(
        self,
        build_id: int,
        request: DeploymentRequest,
        actor: ReviewActor,
        adapter: DeploymentAdapter | None = None,
    ) -> DeploymentORM:
        _operator(actor)
        build = self._build(build_id)
        if build.environment != request.environment.value:
            raise PropertyWorkflowError("deployment_environment_build_mismatch")
        prop = self._property(build.property_id)
        selected = adapter or LocalFilesystemDeploymentAdapter()
        if selected.public_capable:
            raise PropertyWorkflowError("public_deployment_adapter_not_approved")
        gates = self._deployment_gates(prop, build, request)
        if request.environment == BuildEnvironment.production:
            failed = sorted(key for key, passed in gates.items() if not passed)
            if failed:
                raise PropertyWorkflowError(
                    "production_deployment_gates_failed", {"failed_gates": failed}
                )
        prior = self.session.scalar(
            select(DeploymentORM)
            .where(
                DeploymentORM.property_id == prop.id,
                DeploymentORM.environment == request.environment.value,
                DeploymentORM.status == DeploymentStatus.deployed.value,
            )
            .order_by(DeploymentORM.id.desc())
        )
        destination = (
            self.deployment_root
            / prop.id
            / request.environment.value
            / build.build_sha256
        )
        result = await selected.deploy(Path(build.output_path), destination)
        registration = self._verified_registration(prop.id)
        active = self._active_assignment(prop.id)
        compliance = self._approved_compliance(build.id)
        now = _now()
        row = DeploymentORM(
            property_id=prop.id,
            site_build_id=build.id,
            domain_registration_id=registration.id if registration else None,
            provider_assignment_id=active.id if active else None,
            compliance_review_id=compliance.id if compliance else None,
            previous_deployment_id=prior.id if prior else None,
            environment=request.environment.value,
            adapter_name=result.adapter_name,
            status=DeploymentStatus.deployed.value,
            url=result.url,
            local_only=result.local_only,
            operator_user_id=actor.actor_id,
            operator_confirmation=request.operator_confirmation,
            confirmation_reason=request.confirmation_reason,
            neutral_pilot_mode=request.neutral_pilot_mode,
            gate_snapshot=gates,
            deployed_at=now,
        )
        self.session.add(row)
        self._write_provider_runtime_file(destination, active)
        if prior is not None:
            prior.status = DeploymentStatus.rolled_back.value
        if request.environment == BuildEnvironment.staging:
            prop.status = PropertyStatus.staging.value
        if request.environment == BuildEnvironment.production:
            prop.status = PropertyStatus.production.value
        self.session.flush()
        return row

    async def rollback(
        self,
        property_id: str,
        request: RollbackRequest,
        actor: ReviewActor,
        adapter: DeploymentAdapter | None = None,
    ) -> DeploymentORM:
        _operator(actor)
        if not request.operator_confirmation:
            raise PropertyWorkflowError("rollback_operator_confirmation_required")
        target = self.session.get(DeploymentORM, request.target_deployment_id)
        if target is None or target.property_id != property_id:
            raise PropertyWorkflowError("rollback_target_not_found")
        if target.environment != BuildEnvironment.production.value:
            raise PropertyWorkflowError("rollback_target_must_be_production")
        current = self.session.scalar(
            select(DeploymentORM)
            .where(
                DeploymentORM.property_id == property_id,
                DeploymentORM.environment == BuildEnvironment.production.value,
                DeploymentORM.status == DeploymentStatus.deployed.value,
            )
            .order_by(DeploymentORM.id.desc())
        )
        if current is None or current.id == target.id:
            raise PropertyWorkflowError("rollback_requires_different_active_deployment")
        build = self._build(target.site_build_id)
        selected = adapter or LocalFilesystemDeploymentAdapter()
        if selected.public_capable:
            raise PropertyWorkflowError("public_deployment_adapter_not_approved")
        destination = (
            self.deployment_root / property_id / "production" / build.build_sha256
        )
        result = await selected.deploy(Path(build.output_path), destination)
        active_assignment = self._active_assignment(property_id)
        self._write_provider_runtime_file(destination, active_assignment)
        current.status = DeploymentStatus.rolled_back.value
        row = DeploymentORM(
            property_id=property_id,
            site_build_id=build.id,
            domain_registration_id=target.domain_registration_id,
            provider_assignment_id=(
                active_assignment.id if active_assignment is not None else None
            ),
            compliance_review_id=target.compliance_review_id,
            previous_deployment_id=current.id,
            rollback_of_deployment_id=current.id,
            environment=BuildEnvironment.production.value,
            adapter_name=result.adapter_name,
            status=DeploymentStatus.deployed.value,
            url=result.url,
            local_only=True,
            operator_user_id=actor.actor_id,
            operator_confirmation=True,
            confirmation_reason=request.reason,
            neutral_pilot_mode=target.neutral_pilot_mode,
            gate_snapshot={"rollback_target_verified": True, "operator_confirmation": True},
            deployed_at=_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def summary(self, property_id: str) -> dict[str, Any]:
        prop = self._property(property_id)
        versions = list(
            self.session.scalars(
                select(PropertyVersionORM)
                .where(PropertyVersionORM.property_id == property_id)
                .order_by(PropertyVersionORM.version.desc())
            )
        )
        domains = list(
            self.session.scalars(
                select(DomainCandidateORM)
                .where(DomainCandidateORM.property_id == property_id)
                .order_by(DomainCandidateORM.id)
            )
        )
        registrations = list(
            self.session.scalars(
                select(DomainRegistrationORM)
                .where(DomainRegistrationORM.property_id == property_id)
                .order_by(DomainRegistrationORM.id)
            )
        )
        configs = list(
            self.session.scalars(
                select(SiteConfigORM)
                .where(SiteConfigORM.property_id == property_id)
                .order_by(SiteConfigORM.version.desc())
            )
        )
        builds = list(
            self.session.scalars(
                select(SiteBuildORM)
                .where(SiteBuildORM.property_id == property_id)
                .order_by(SiteBuildORM.id.desc())
            )
        )
        deployments = list(
            self.session.scalars(
                select(DeploymentORM)
                .where(DeploymentORM.property_id == property_id)
                .order_by(DeploymentORM.id.desc())
            )
        )
        assignments = list(
            self.session.scalars(
                select(ProviderAssignmentORM)
                .where(ProviderAssignmentORM.property_id == property_id)
                .order_by(ProviderAssignmentORM.id.desc())
            )
        )
        assets = list(
            self.session.scalars(
                select(AssetORM)
                .where(AssetORM.property_id == property_id)
                .order_by(AssetORM.id.desc())
            )
        )
        compliance = list(
            self.session.scalars(
                select(ComplianceReviewORM)
                .where(ComplianceReviewORM.property_id == property_id)
                .order_by(ComplianceReviewORM.id.desc())
            )
        )
        return {
            "property": self._property_payload(prop),
            "versions": [self._row_payload(item) for item in versions],
            "domain_candidates": [self._row_payload(item) for item in domains],
            "domain_registrations": [self._row_payload(item) for item in registrations],
            "site_configs": [self._row_payload(item) for item in configs],
            "site_builds": [self._row_payload(item) for item in builds],
            "deployments": [self._row_payload(item) for item in deployments],
            "provider_assignments": [self._row_payload(item) for item in assignments],
            "assets": [self._row_payload(item) for item in assets],
            "compliance_reviews": [self._row_payload(item) for item in compliance],
        }

    def list_properties(self) -> list[dict[str, Any]]:
        return [
            self._property_payload(row)
            for row in self.session.scalars(
                select(PropertyORM).order_by(PropertyORM.updated_at.desc())
            )
        ]

    def _deployment_gates(
        self,
        prop: PropertyORM,
        build: SiteBuildORM,
        request: DeploymentRequest,
    ) -> dict[str, bool]:
        opportunity = self.session.get(OpportunityORM, prop.opportunity_id)
        config = self._config(build.site_config_id)
        registration = self._verified_registration(prop.id)
        active = self._active_assignment(prop.id)
        compliance = self._approved_compliance(build.id)
        profile = self._routing_profile(prop.id)
        neutral_pilot = request.neutral_pilot_mode and bool(
            request.neutral_pilot_reason.strip()
        )
        return {
            "approved_opportunity": (
                opportunity is not None
                and opportunity.status == "approved_for_property"
            ),
            "approved_site_config": config.status == SiteConfigStatus.approved.value,
            "passing_build": (
                build.status == "passed"
                and bool(build.validation_report.get("passed"))
            ),
            "active_provider_or_neutral_pilot": active is not None or neutral_pilot,
            "provider_claims_reviewed": (
                neutral_pilot or (active is not None and active.claims_reviewed_at is not None)
            ),
            "compliance_approved": compliance is not None,
            "domain_and_dns_verified": (
                registration is not None and prop.domain == registration.domain
            ),
            "routing_healthy": profile.routing_health_status in {"healthy", "active", "ok"},
            "analytics_verified": prop.analytics_config.get("verified") is True,
            "operator_confirmation": (
                request.operator_confirmation and bool(request.confirmation_reason.strip())
            ),
            "local_fail_closed_adapter": True,
        }

    def _validate_assets(self, property_id: str, asset_ids: list[int]) -> None:
        for asset_id in asset_ids:
            asset = self.session.get(AssetORM, asset_id)
            if asset is None or asset.property_id != property_id:
                raise PropertyWorkflowError("asset_not_found", {"asset_id": asset_id})
            if not asset.approved:
                raise PropertyWorkflowError(
                    "site_config_requires_approved_assets", {"asset_id": asset_id}
                )

    def _asset_provenance(
        self,
        property_id: str,
        asset_ids: list[int],
    ) -> list[dict[str, Any]]:
        provenance: list[dict[str, Any]] = []
        for asset_id in asset_ids:
            asset = self.session.get(AssetORM, asset_id)
            if asset is None or asset.property_id != property_id or not asset.approved:
                raise PropertyWorkflowError(
                    "site_config_requires_approved_assets",
                    {"asset_id": asset_id},
                )
            provenance.append(
                {
                    "id": asset.id,
                    "asset_type": asset.asset_type,
                    "source_provider": asset.source_provider,
                    "source_url": asset.source_url,
                    "attribution": asset.attribution,
                    "license_metadata": asset.license_metadata,
                    "alt_text": asset.alt_text,
                    "content_sha256": asset.content_sha256,
                    "approved_by": asset.approved_by,
                }
            )
        return provenance

    def _update_provider_runtime_configuration(
        self,
        property_id: str,
        assignment: ProviderAssignmentORM,
    ) -> None:
        deployments = self.session.scalars(
            select(DeploymentORM).where(
                DeploymentORM.property_id == property_id,
                DeploymentORM.status == DeploymentStatus.deployed.value,
                DeploymentORM.local_only.is_(True),
            )
        )
        for deployment in deployments:
            parsed = urlparse(deployment.url)
            if parsed.scheme == "file":
                self._write_provider_runtime_file(
                    Path(unquote(parsed.path)).parent,
                    assignment,
                )

    @staticmethod
    def _write_provider_runtime_file(
        destination: Path,
        assignment: ProviderAssignmentORM | None,
    ) -> None:
        payload: dict[str, Any] = {}
        if assignment is not None:
            payload = {
                "assignment_id": assignment.id,
                "public_business_name": assignment.public_business_name,
                "hours": assignment.hours,
                "service_radius": assignment.service_radius,
                "approved_claims": assignment.approved_claims,
                "credentials": assignment.credentials,
                "license_numbers": assignment.license_numbers,
                "logo_asset_id": assignment.logo_asset_id,
                "provider_photos": assignment.provider_photos,
                "claims_reviewed_at": (
                    assignment.claims_reviewed_at.isoformat()
                    if assignment.claims_reviewed_at
                    else None
                ),
            }
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "provider-config.json").write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

    def _validate_provider_content(
        self,
        property_id: str,
        details: dict[str, Any],
    ) -> None:
        if not details:
            return
        assignment_id = details.get("assignment_id")
        assignment = self.session.get(ProviderAssignmentORM, assignment_id)
        if (
            assignment is None
            or assignment.property_id != property_id
            or assignment.claims_reviewed_at is None
        ):
            raise PropertyWorkflowError("provider_details_require_reviewed_assignment")

    def _snapshot(
        self,
        prop: PropertyORM,
        actor: ReviewActor,
        reason: str,
    ) -> PropertyVersionORM:
        snapshot = self._property_payload(prop)
        row = PropertyVersionORM(
            property_id=prop.id,
            version=prop.current_version,
            snapshot=snapshot,
            snapshot_sha256=_json_hash(snapshot),
            changed_by=actor.actor_id,
            change_reason=reason,
        )
        self.session.add(row)
        self.session.flush()
        return row

    @staticmethod
    def _property_payload(row: PropertyORM) -> dict[str, Any]:
        return {
            "id": row.id,
            "opportunity_id": row.opportunity_id,
            "neutral_brand": row.neutral_brand,
            "domain": row.domain,
            "service_family_id": row.service_family_id,
            "market_id": row.market_id,
            "public_tracking_number": row.public_tracking_number,
            "public_contact_email": row.public_contact_email,
            "status": row.status,
            "active_site_config_version": row.active_site_config_version,
            "analytics_config": row.analytics_config,
            "current_version": row.current_version,
        }

    @staticmethod
    def _row_payload(row: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for column in row.__table__.columns:
            value = getattr(row, column.name)
            payload[column.name] = value.isoformat() if isinstance(value, datetime) else value
        return payload

    def _property(self, property_id: str) -> PropertyORM:
        row = self.session.get(PropertyORM, property_id)
        if row is None:
            raise PropertyWorkflowError("property_not_found")
        return row

    def _candidate(self, candidate_id: int) -> DomainCandidateORM:
        row = self.session.get(DomainCandidateORM, candidate_id)
        if row is None:
            raise PropertyWorkflowError("domain_candidate_not_found")
        return row

    def _registration(self, registration_id: int) -> DomainRegistrationORM:
        row = self.session.get(DomainRegistrationORM, registration_id)
        if row is None:
            raise PropertyWorkflowError("domain_registration_not_found")
        return row

    def _assignment(self, assignment_id: int) -> ProviderAssignmentORM:
        row = self.session.get(ProviderAssignmentORM, assignment_id)
        if row is None:
            raise PropertyWorkflowError("provider_assignment_not_found")
        return row

    def _config(self, config_id: int) -> SiteConfigORM:
        row = self.session.get(SiteConfigORM, config_id)
        if row is None:
            raise PropertyWorkflowError("site_config_not_found")
        return row

    def _build(self, build_id: int) -> SiteBuildORM:
        row = self.session.get(SiteBuildORM, build_id)
        if row is None:
            raise PropertyWorkflowError("site_build_not_found")
        return row

    def _routing_profile(self, property_id: str) -> PropertyRoutingProfileORM:
        row = self.session.scalar(
            select(PropertyRoutingProfileORM).where(
                PropertyRoutingProfileORM.property_id == property_id
            )
        )
        if row is None:
            raise PropertyWorkflowError("routing_profile_not_found")
        return row

    def _active_assignment(self, property_id: str) -> ProviderAssignmentORM | None:
        return self.session.scalar(
            select(ProviderAssignmentORM).where(
                ProviderAssignmentORM.property_id == property_id,
                ProviderAssignmentORM.status == ProviderAssignmentStatus.active.value,
            )
        )

    def _verified_registration(self, property_id: str) -> DomainRegistrationORM | None:
        return self.session.scalar(
            select(DomainRegistrationORM)
            .where(
                DomainRegistrationORM.property_id == property_id,
                DomainRegistrationORM.status
                == DomainRegistrationStatus.dns_verified.value,
            )
            .order_by(DomainRegistrationORM.id.desc())
        )

    def _approved_compliance(self, build_id: int) -> ComplianceReviewORM | None:
        return self.session.scalar(
            select(ComplianceReviewORM)
            .where(
                ComplianceReviewORM.site_build_id == build_id,
                ComplianceReviewORM.status == ComplianceStatus.approved.value,
            )
            .order_by(ComplianceReviewORM.id.desc())
        )
