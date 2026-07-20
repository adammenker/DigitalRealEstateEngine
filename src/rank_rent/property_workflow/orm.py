from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from rank_rent.db.base import Base
from rank_rent.db.orm import TimestampMixin
from rank_rent.lead_routing.orm import ProviderAssignmentORM


class PropertyORM(TimestampMixin, Base):
    __tablename__ = "properties"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id"), unique=True, index=True
    )
    neutral_brand: Mapped[str] = mapped_column(String(160))
    domain: Mapped[str | None] = mapped_column(String(253), nullable=True, unique=True)
    service_family_id: Mapped[int] = mapped_column(ForeignKey("service_families.id"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    public_tracking_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    public_contact_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    active_site_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analytics_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    current_version: Mapped[int] = mapped_column(Integer, default=1)


class PropertyVersionORM(TimestampMixin, Base):
    __tablename__ = "property_versions"
    __table_args__ = (
        UniqueConstraint("property_id", "version", name="uq_property_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    changed_by: Mapped[str] = mapped_column(String(120))
    change_reason: Mapped[str] = mapped_column(Text)


class DomainCandidateORM(TimestampMixin, Base):
    __tablename__ = "property_domain_candidates"
    __table_args__ = (
        UniqueConstraint("property_id", "domain", name="uq_property_domain_candidate"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(String(40), default="generated", index=True)
    generation_method: Mapped[str] = mapped_column(String(80), default="local-deterministic-v1")
    availability_status: Mapped[str] = mapped_column(String(40), default="unchecked")
    availability_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    availability_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decision_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DomainRegistrationORM(TimestampMixin, Base):
    __tablename__ = "domain_registrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    domain_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("property_domain_candidates.id"), unique=True
    )
    domain: Mapped[str] = mapped_column(String(253), unique=True)
    registrar_name: Mapped[str] = mapped_column(String(120), default="manual")
    status: Mapped[str] = mapped_column(String(40), index=True)
    purchase_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    purchase_approved_by: Mapped[str] = mapped_column(String(120))
    purchase_approval_reason: Mapped[str] = mapped_column(Text)
    purchase_approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    external_reference: Mapped[str | None] = mapped_column(String(240), nullable=True)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_dns_records: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    observed_dns_records: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    dns_evidence_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    dns_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dns_verified_by: Mapped[str | None] = mapped_column(String(120), nullable=True)


class AssetORM(TimestampMixin, Base):
    __tablename__ = "property_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    asset_type: Mapped[str] = mapped_column(String(80))
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_provider: Mapped[str] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_metadata: Mapped[dict[str, Any]] = mapped_column(JSON)
    alt_text: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approval_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SiteConfigORM(TimestampMixin, Base):
    __tablename__ = "property_site_configs"
    __table_args__ = (
        UniqueConstraint("property_id", "version", name="uq_site_config_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    schema_version: Mapped[str] = mapped_column(String(40), default="site-config-v1")
    config_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    config_sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(120))
    change_reason: Mapped[str] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approval_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SiteBuildORM(TimestampMixin, Base):
    __tablename__ = "site_builds"
    __table_args__ = (
        UniqueConstraint(
            "site_config_id",
            "environment",
            "build_sha256",
            name="uq_reproducible_site_build",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    site_config_id: Mapped[int] = mapped_column(
        ForeignKey("property_site_configs.id"), index=True
    )
    environment: Mapped[str] = mapped_column(String(20), index=True)
    builder_version: Mapped[str] = mapped_column(String(40))
    build_sha256: Mapped[str] = mapped_column(String(64), index=True)
    output_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), index=True)
    file_count: Mapped[int] = mapped_column(Integer)
    total_bytes: Mapped[int] = mapped_column(Integer)
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSON)
    manifest: Mapped[dict[str, str]] = mapped_column(JSON)


class ComplianceReviewORM(TimestampMixin, Base):
    __tablename__ = "compliance_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    site_config_id: Mapped[int] = mapped_column(
        ForeignKey("property_site_configs.id"), index=True
    )
    site_build_id: Mapped[int] = mapped_column(ForeignKey("site_builds.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    checklist: Mapped[dict[str, bool]] = mapped_column(JSON)
    validation_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    reviewer_user_id: Mapped[str] = mapped_column(String(120), index=True)
    notes: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeploymentORM(TimestampMixin, Base):
    __tablename__ = "property_deployments"
    __table_args__ = (
        Index(
            "ix_property_deployments_current",
            "property_id",
            "environment",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    site_build_id: Mapped[int] = mapped_column(ForeignKey("site_builds.id"))
    domain_registration_id: Mapped[int | None] = mapped_column(
        ForeignKey("domain_registrations.id"), nullable=True
    )
    provider_assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_assignments.id"), nullable=True
    )
    compliance_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("compliance_reviews.id"), nullable=True
    )
    previous_deployment_id: Mapped[int | None] = mapped_column(
        ForeignKey("property_deployments.id"), nullable=True
    )
    rollback_of_deployment_id: Mapped[int | None] = mapped_column(
        ForeignKey("property_deployments.id"), nullable=True
    )
    environment: Mapped[str] = mapped_column(String(20), index=True)
    adapter_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), index=True)
    url: Mapped[str] = mapped_column(Text)
    local_only: Mapped[bool] = mapped_column(Boolean, default=True)
    operator_user_id: Mapped[str] = mapped_column(String(120))
    operator_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmation_reason: Mapped[str] = mapped_column(Text)
    neutral_pilot_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    gate_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# Workstream J's provider_assignments table remains authoritative. The alias gives
# Workstream I vocabulary without creating a second assignment lifecycle.
ActiveProviderAssignmentORM = ProviderAssignmentORM


class ImmutablePropertyWorkflowRecordError(RuntimeError):
    pass


def _reject_immutable_record_mutation(
    _mapper: object,
    _connection: object,
    target: object,
) -> None:
    state: Any = inspect(target)
    changed = [
        attribute.key
        for attribute in state.mapper.column_attrs
        if attribute.key not in {"updated_at"}
        and state.attrs[attribute.key].history.has_changes()
    ]
    if changed:
        raise ImmutablePropertyWorkflowRecordError(
            "Immutable property workflow record cannot be changed: "
            + ", ".join(sorted(changed))
        )


for immutable_model in (PropertyVersionORM, SiteBuildORM, ComplianceReviewORM):
    event.listen(immutable_model, "before_update", _reject_immutable_record_mutation)
