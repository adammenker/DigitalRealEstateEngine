from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from rank_rent.db.base import Base
from rank_rent.db.orm import TimestampMixin


class OpportunityReviewORM(TimestampMixin, Base):
    __tablename__ = "opportunity_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    prior_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    review_state: Mapped[str] = mapped_column(String(40), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    reviewer_user_id: Mapped[str] = mapped_column(String(120), index=True)
    reviewer_role: Mapped[str] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(80))
    decision_reason: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_version: Mapped[int] = mapped_column(Integer)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceOverrideORM(TimestampMixin, Base):
    __tablename__ = "evidence_overrides"
    __table_args__ = (
        UniqueConstraint("reverses_override_id", name="uq_evidence_override_reversal"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    override_kind: Mapped[str] = mapped_column(String(60), index=True)
    target_record_id: Mapped[int] = mapped_column(Integer)
    field_name: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(20), default="apply")
    original_value: Mapped[Any] = mapped_column(JSON)
    new_value: Mapped[Any] = mapped_column(JSON)
    actor_user_id: Mapped[str] = mapped_column(String(120), index=True)
    actor_role: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    score_impact: Mapped[float] = mapped_column(Float)
    score_impact_explanation: Mapped[str] = mapped_column(Text)
    reverses_override_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_overrides.id"), nullable=True, index=True
    )


class DiscoveryTemplateORM(TimestampMixin, Base):
    __tablename__ = "discovery_templates"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "name", name="uq_discovery_template_owner_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    owner_user_id: Mapped[str] = mapped_column(String(120), index=True)
    service_family_id: Mapped[int] = mapped_column(ForeignKey("service_families.id"))
    market_filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prefilter_profile: Mapped[str] = mapped_column(String(80))
    testing_profile: Mapped[str] = mapped_column(String(80), default="testing")
    full_profile: Mapped[str] = mapped_column(String(80), default="full")
    budget_usd: Mapped[float] = mapped_column(Float)
    freshness_requirements: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class BatchScanPlanORM(TimestampMixin, Base):
    __tablename__ = "batch_scan_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("discovery_templates.id"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(120), index=True)
    scan_profile: Mapped[str] = mapped_column(String(40))
    data_mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(40), default="planned", index=True)
    aggregate_budget_usd: Mapped[float] = mapped_column(Float)
    aggregate_estimated_cost_usd: Mapped[float] = mapped_column(Float)
    approved_max_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    confirmation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BatchScanPlanItemORM(TimestampMixin, Base):
    __tablename__ = "batch_scan_plan_items"
    __table_args__ = (
        UniqueConstraint(
            "batch_plan_id",
            "opportunity_id",
            name="uq_batch_scan_plan_opportunity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_plan_id: Mapped[int] = mapped_column(ForeignKey("batch_scan_plans.id"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="planned")
    estimated_cost_usd: Mapped[float] = mapped_column(Float)
    scan_plan_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    scan_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_runs.id"), nullable=True, unique=True
    )
